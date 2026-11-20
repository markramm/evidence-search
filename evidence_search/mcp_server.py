"""Zero-dependency Phase 2 MCP JSON-RPC 2.0 server over `evidence-search`.

Exposes `evidence_sources`, `evidence_search`, `evidence_investigate`,
`evidence_extract`, `evidence_record`, and `evidence_limits` as standard Model
Context Protocol (MCP) tools returning structured JSON payloads (`Outcome.to_dict()`).

Run as stdio server via `evidence-search mcp` or `evidence-search-mcp`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, TextIO

from . import __version__
from .core import browser as _browser
from .core import extract as _extract
from .core import investigate as _investigate
from .core.http import fetch as _http_fetch
from .core.limits import Limiter, POLICIES, bucket_for_url, effective_policy
from .core.registry import all_specs, get_spec
from .core.results import AccessBlocker, Blocker
from .core.store import Store

PROTOCOL_VERSION = "2024-11-05"


def list_mcp_tools() -> list[dict[str, Any]]:
    """Return MCP tool definitions exposing evidence-search capabilities."""
    return [
        {
            "name": "evidence_sources",
            "description": "List registered evidence-search sources/plugins with corpus, counts, and epistemic caveats.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Optional single source/plugin name."},
                },
            },
        },
        {
            "name": "evidence_search",
            "description": "Search one registered source or plugin and return a typed Outcome (Hit, VerifiedAbsence, AccessBlocker, RateLimited, AwaitingHuman).",
            "inputSchema": {
                "type": "object",
                "required": ["source", "query"],
                "properties": {
                    "source": {"type": "string", "description": "Source or plugin name (e.g. courtlistener, usaspending, web, datagov_catalog)."},
                    "query": {"type": "string", "description": "Search query string or target identifier."},
                    "limit": {"type": "integer", "default": 10},
                    "use_cache": {"type": "boolean", "default": True},
                    "options": {"type": "object", "description": "Optional source-specific flags (e.g. agency, state, provider, where)."},
                },
            },
        },
        {
            "name": "evidence_investigate",
            "description": "Run a coordinated cross-source sweep and automatic secondary identifier pivots (CIK, EIN, UEI, domains).",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Target entity, contractor, or organization name."},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "domain": {"type": "string", "description": "Optional domain to run crtsh + wayback + TLS pivots."},
                    "limit": {"type": "integer", "default": 5},
                    "use_cache": {"type": "boolean", "default": True},
                },
            },
        },
        {
            "name": "evidence_extract",
            "description": "Fetch a URL or local text/HTML file with shared rate-limit accounting and extract grep matches, identifiers, tables, or readable text.",
            "inputSchema": {
                "type": "object",
                "required": ["target"],
                "properties": {
                    "target": {"type": "string", "description": "HTTP(S) URL or local file path."},
                    "grep": {"type": "array", "items": {"type": "string"}},
                    "ids": {"type": "boolean", "default": False},
                    "tables": {"type": "boolean", "default": False},
                    "text": {"type": "boolean", "default": False},
                    "max_chars": {"type": "integer", "default": 4000},
                },
            },
        },
        {
            "name": "evidence_record",
            "description": "List or inspect full raw upstream records stored automatically in SQLite by searches.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "source": {"type": "string"},
                    "query": {"type": "string"},
                    "fields": {"type": "string", "description": "Comma-separated payload fields to keep."},
                },
            },
        },
        {
            "name": "evidence_limits",
            "description": "Inspect shared SQLite rate-limit usage, active 429 blocks, and backoff timers.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Optional source name filter."},
                },
            },
        },
    ]


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
        "isError": is_error,
    }


def _tool_sources(args: dict[str, Any]) -> dict[str, Any]:
    src = args.get("source")
    if src:
        sp = get_spec(str(src))
        if sp is None:
            return _text_result({"error": f"Unknown source {src!r}"}, is_error=True)
        return _text_result(sp.to_dict())
    return _text_result([s.to_dict() for s in all_specs()])


def _tool_search(args: dict[str, Any], *, store: Store, limiter: Limiter) -> dict[str, Any]:
    source = str(args.get("source") or "").strip()
    query = str(args.get("query") or "")
    if not source or not query:
        return _text_result({"error": "'source' and 'query' are required"}, is_error=True)
    limit = int(args.get("limit") or 10)
    use_cache = bool(args.get("use_cache", True))
    extra = args.get("options") if isinstance(args.get("options"), dict) else None
    out = _investigate.run_single_source(
        source,
        query,
        limit=limit,
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        extra=extra,
    )
    return _text_result(out.to_dict())


def _tool_investigate(args: dict[str, Any], *, store: Store, limiter: Limiter) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return _text_result({"error": "'query' is required"}, is_error=True)
    report = _investigate.run_investigation(
        query,
        sources=args.get("sources"),
        domain=args.get("domain"),
        limit=int(args.get("limit") or 5),
        store=store,
        limiter=limiter,
        use_cache=bool(args.get("use_cache", True)),
    )
    return _text_result(report)


def _tool_extract(args: dict[str, Any], *, store: Store, limiter: Limiter) -> dict[str, Any]:
    from .cli import EXTRACT_MIN_VISIBLE_CHARS, _read_pdf

    target = str(args.get("target") or "").strip()
    if not target:
        return _text_result({"error": "'target' is required"}, is_error=True)

    is_url = target.startswith(("http://", "https://"))
    allow_ocr = not bool(args.get("no_ocr", False))
    ocr_pages = int(args.get("ocr_pages") or 50)
    pdf_provenance = None
    render_backend = None

    if is_url:
        cdp_active = bool((os.environ.get(_browser.CDP_ENV) or "").strip())
        bucket = bucket_for_url(target)
        ok, retry, why = limiter.reserve(bucket, wait_s=10.0)
        if not ok:
            return _text_result(
                {"error": "rate_limited", "source": bucket, "retry_after_s": retry, "detail": why},
                is_error=True,
            )
        raw, err = _http_fetch(target, source=bucket, query=target)
        limiter.note_outcome(bucket, err, count_failure=False)

        needs_cdp = False
        if cdp_active:
            if err is not None and isinstance(err, AccessBlocker) and err.mechanism is Blocker.JS_ONLY:
                needs_cdp = True
            elif err is None and raw and not raw.lstrip()[:5].startswith("%PDF"):
                if len(_extract.page_text(raw)) < EXTRACT_MIN_VISIBLE_CHARS:
                    needs_cdp = True
        if needs_cdp:
            b_ok, _, _ = limiter.reserve(bucket, wait_s=10.0, honor_backoff=False)
            if b_ok:
                b_raw, b_err = _browser.fetch(target, source="extract", query=target, store=store, allow_ungated_js=True)
                if b_raw and not b_err:
                    raw, err = b_raw, None
                    render_backend = _browser.current_backend(headless=True)

        if err is not None:
            return _text_result(err.to_dict(), is_error=True)
        raw = raw or ""

        if raw.lstrip()[:5].startswith("%PDF"):
            ok_b, retry_b, why_b = limiter.reserve(bucket, wait_s=10.0)
            if not ok_b:
                return _text_result(
                    {"error": "rate_limited", "source": bucket, "retry_after_s": retry_b, "detail": why_b},
                    is_error=True,
                )
            bin_bytes, b_err = _http_fetch(target, source=bucket, query=target, binary=True)
            limiter.note_outcome(bucket, b_err, count_failure=False)
            if b_err is not None:
                return _text_result(b_err.to_dict(), is_error=True)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                tf.write(bin_bytes)
                tmp_pdf = Path(tf.name)
            try:
                raw, pdf_provenance, pdf_blk = _read_pdf(tmp_pdf, target, allow_ocr, ocr_pages, "remote PDF")
            finally:
                tmp_pdf.unlink(missing_ok=True)
            if pdf_blk is not None:
                return _text_result(pdf_blk.to_dict(), is_error=True)
    else:
        p = Path(target).expanduser()
        if not p.exists():
            return _text_result({"error": f"File not found: {target}"}, is_error=True)
        head = b""
        try:
            with open(p, "rb") as fh:
                head = fh.read(5)
        except OSError:
            pass
        if head.startswith(b"%PDF"):
            raw, pdf_provenance, pdf_blk = _read_pdf(p, target, allow_ocr, ocr_pages)
            if pdf_blk is not None:
                return _text_result(pdf_blk.to_dict(), is_error=True)
        else:
            raw = p.read_text(errors="replace")

    is_html = (is_url or target.endswith((".html", ".htm"))) and not pdf_provenance
    payload: dict[str, Any] = {"target": target}
    if pdf_provenance:
        payload["provenance"] = pdf_provenance
    if render_backend:
        payload["render_backend"] = render_backend
    if args.get("grep"):
        payload["matches"] = _extract.grep(raw, list(args["grep"]), is_html=is_html)
    if args.get("ids"):
        payload["identifiers"] = _extract.identifiers(raw, is_html=is_html)
    if args.get("tables"):
        payload["tables"] = _extract.tables(raw) if is_html else []
    max_chars = int(args.get("max_chars") or 4000)
    if args.get("text") or not any(k in payload for k in ("matches", "identifiers", "tables")):
        txt = _extract.page_text(raw, max_chars) if is_html else raw[:max_chars]
        payload["text"] = txt
    return _text_result(payload)


def _tool_record(args: dict[str, Any], *, store: Store) -> dict[str, Any]:
    rec_id = args.get("record_id")
    if rec_id:
        rec = store.get_record(str(rec_id))
        if not rec:
            return _text_result({"error": f"No record {rec_id!r}"}, is_error=True)
        if args.get("fields"):
            want = {f.strip() for f in str(args["fields"]).split(",") if f.strip()}
            rec = {**rec, "payload": {k: v for k, v in rec["payload"].items() if k in want}}
        return _text_result(rec)
    rows = store.find_records(source=args.get("source"), query=args.get("query"))
    return _text_result({"records": rows, "count": len(rows)})


def _tool_limits(args: dict[str, Any], *, limiter: Limiter) -> dict[str, Any]:
    names = [str(args["source"])] if args.get("source") else sorted(POLICIES)
    rows = []
    for name in names:
        if name not in POLICIES:
            return _text_result({"error": f"Unknown policy source {name!r}"}, is_error=True)
        pol, _ = effective_policy(name)
        st = limiter.status(name)
        rows.append({"source": name, "cacheable": pol.cacheable, **st})
    return _text_result({"limits": rows})


def call_mcp_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    store: Store | None = None,
    limiter: Limiter | None = None,
) -> dict[str, Any]:
    """Execute one named MCP tool synchronously against core evidence-search modules."""
    args = dict(arguments or {})
    store = store or Store()
    limiter = limiter or Limiter(store)

    handlers = {
        "evidence_sources": lambda: _tool_sources(args),
        "evidence_search": lambda: _tool_search(args, store=store, limiter=limiter),
        "evidence_investigate": lambda: _tool_investigate(args, store=store, limiter=limiter),
        "evidence_extract": lambda: _tool_extract(args, store=store, limiter=limiter),
        "evidence_record": lambda: _tool_record(args, store=store),
        "evidence_limits": lambda: _tool_limits(args, limiter=limiter),
    }
    fn = handlers.get(name)
    if fn is None:
        return _text_result({"error": f"Unknown tool {name!r}"}, is_error=True)
    return fn()


def handle_jsonrpc_message(
    message: dict[str, Any],
    *,
    store: Store | None = None,
    limiter: Limiter | None = None,
) -> dict[str, Any] | None:
    """Process one JSON-RPC 2.0 MCP message and return response dict (None for notifications)."""
    method = str(message.get("method") or "")
    msg_id = message.get("id")

    if method == "notifications/initialized" or msg_id is None:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "evidence-search", "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": list_mcp_tools()}}
    if method == "tools/call":
        params = message.get("params") or {}
        tool_name = str(params.get("name") or "")
        tool_args = params.get("arguments") or {}
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": call_mcp_tool(tool_name, tool_args, store=store, limiter=limiter),
        }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio(
    in_stream: TextIO | None = None,
    out_stream: TextIO | None = None,
    *,
    store: Store | None = None,
    limiter: Limiter | None = None,
) -> int:
    """Serve newline-delimited JSON-RPC 2.0 MCP requests over stdio."""
    reader = in_stream or sys.stdin
    writer = out_stream or sys.stdout
    store = store or Store()
    limiter = limiter or Limiter(store)

    for line in reader:
        raw = line.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            writer.write(json.dumps(err) + "\n")
            writer.flush()
            continue

        resp = handle_jsonrpc_message(msg, store=store, limiter=limiter)
        if resp is not None:
            writer.write(json.dumps(resp, default=str) + "\n")
            writer.flush()
    return 0


def main() -> int:
    """Console entry point for `evidence-search-mcp`."""
    return serve_stdio()
