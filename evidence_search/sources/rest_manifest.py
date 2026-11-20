"""Generic declarative JSON REST manifest driver (`"driver": "rest"`).

Powers declarative JSON plugins in `evidence_search/plugins/*.json` or
`~/.evidence-search/plugins/*.json` for CKAN catalogs, Datasette/OpenGov APIs,
and arbitrary JSON REST endpoints with zero boilerplate Python code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote_plus, urlencode

from ..core.limits import Limiter
from ..core.registry import get_spec, spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "rest"


def _flatten_scalar(val: Any) -> str:
    if isinstance(val, dict):
        for k in ("title", "name", "display_name", "label", "value"):
            if val.get(k) not in (None, ""):
                return str(val[k]).strip()
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, list):
        return ", ".join(_flatten_scalar(v) for v in val[:4] if v not in (None, ""))
    return str(val).strip() if val is not None else ""


def _read_dotted(root: Any, dotted: str | None) -> Any:
    if not dotted:
        return None
    cur = root
    for part in dotted.split("."):
        if not part:
            continue
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _format_item_template(tmpl: str, row: dict) -> str:
    out = tmpl
    for k, v in row.items():
        token = "{" + str(k) + "}"
        if token in out and not isinstance(v, (dict, list)) and v is not None:
            out = out.replace(token, str(v).strip())
    return out


def _parse_rest(
    body: str,
    *,
    query_url: str,
    source_name: str,
    rows_path: str = "results",
    total_path: str | None = None,
    url_field: str | None = None,
    item_url_template: str | None = None,
    title_fields: list[str] | None = None,
    snippet_fields: list[str] | None = None,
    store: Store | None = None,
) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"REST source {source_name!r} returned non-JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"REST source {source_name!r} expected JSON object, got {type(data).__name__}")
    if data.get("error"):
        raise ValueError(f"REST API error: {data['error']}")
    if data.get("success") is False:
        raise ValueError(f"REST API returned success=false ({data.get('help') or 'error'})")

    parts = [p for p in (rows_path or "results").split(".") if p]
    if not parts:
        raise ValueError("REST manifest rows_path must not be empty")

    parent: Any = data
    for step in parts[:-1]:
        if not isinstance(parent, dict) or step not in parent:
            raise ValueError(f"missing parent key {step!r} along rows_path {rows_path!r}")
        parent = parent[step]
    leaf_key = parts[-1]

    local_total_key: str | None = None
    ext_total: Any = None
    if total_path:
        t_parts = [p for p in total_path.split(".") if p]
        if t_parts[:-1] == parts[:-1]:
            local_total_key = t_parts[-1]
        else:
            ext_total = _read_dotted(data, total_path)
    elif "count" in parent:
        local_total_key = "count"

    rows = json_rows(parent, leaf_key, total_key=local_total_key)
    if isinstance(rows, RecognizedEmpty):
        if isinstance(ext_total, (int, float)) and not isinstance(ext_total, bool) and ext_total > 0:
            raise ValueError(f"REST response reports {total_path}={ext_total} beside empty rows")
        return rows

    total_val = ext_total if ext_total is not None else (
        parent.get(local_total_key) if isinstance(parent, dict) and local_total_key else None
    )
    total_matches = (
        int(total_val)
        if isinstance(total_val, (int, float)) and not isinstance(total_val, bool)
        else len(rows)
    )

    out: list[Result] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_url = ""
        if item_url_template:
            cand = _format_item_template(item_url_template, row)
            if "{" not in cand:
                item_url = cand
        if not item_url:
            for key in [url_field] + ["url", "html_url", "link"]:
                if key and row.get(key):
                    item_url = str(row[key]).strip()
                    break
        if not item_url:
            item_url = query_url

        if title_fields:
            t_parts_v = [
                _flatten_scalar(_read_dotted(row, f) if "." in f else row.get(f))
                for f in title_fields
                if _flatten_scalar(_read_dotted(row, f) if "." in f else row.get(f))
            ]
            title = " — ".join(t_parts_v)[:180]
        else:
            title = ""
        if not title:
            t_fallback = [
                f"{k}={_flatten_scalar(v)}"
                for k, v in row.items()
                if v not in (None, "", [], {}) and not isinstance(v, (dict, list))
            ][:4]
            title = " | ".join(t_fallback)[:180] or f"{source_name} record"

        if snippet_fields:
            s_parts = [
                f"{f}: {_flatten_scalar(_read_dotted(row, f) if '.' in f else row.get(f))}"
                for f in snippet_fields
                if _flatten_scalar(_read_dotted(row, f) if "." in f else row.get(f))
            ]
        else:
            s_parts = [
                f"{k}: {_flatten_scalar(v)}"
                for k, v in row.items()
                if v not in (None, "", [], {}) and not isinstance(v, (dict, list))
            ][:6]

        row_id = str(row.get("id") or row.get("slug") or row.get("name") or "").strip()
        if not row_id:
            row_id = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:12]
        rid = f"{source_name}:{row_id}"
        if store is not None:
            try:
                store.put_record(rid, source_name, query_url, row)
            except Exception:
                pass
        out.append(
            Result(
                url=item_url,
                title=title,
                snippet=" | ".join(s_parts)[:360],
                source=source_name,
                engines=[source_name],
                meta={"record_id": rid, "row": row, "total_matches": total_matches},
            )
        )
    if not out:
        raise ValueError(f"REST rows at {rows_path!r} contained no dict records")
    return out


def query(
    text_query: str = "",
    *,
    source_name: str = SOURCE,
    endpoint: str = "",
    url_template: str | None = None,
    query_param: str = "q",
    limit_param: str = "rows",
    static_params: dict[str, Any] | None = None,
    rows_path: str = "results",
    total_path: str | None = None,
    url_field: str | None = None,
    item_url_template: str | None = None,
    title_fields: list[str] | None = None,
    snippet_fields: list[str] | None = None,
    corpus: str | None = None,
    not_searched: list[str] | None = None,
    caveats: list[str] | None = None,
    exact_match_supported: bool | None = False,
    headers: dict[str, str] | None = None,
    limit: int = 20,
    store: Store | None = None,
    limiter: Limiter | None = None,
    use_cache: bool = True,
):
    """Query a declarative JSON REST endpoint with full absence-safety."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (text_query or "").strip()
    cap = max(1, min(int(limit), 200))

    if url_template:
        url = (
            url_template.replace("{query}", quote_plus(q_clean))
            .replace("{raw_query}", q_clean)
            .replace("{limit}", str(cap))
        )
        base_id = url_template.split("?", 1)[0]
    elif endpoint:
        params: dict[str, str] = {
            str(k): str(v) for k, v in (static_params or {}).items() if v is not None
        }
        if q_clean:
            params[query_param] = q_clean
        if limit_param:
            params[limit_param] = str(cap)
        sep = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{sep}{urlencode(params)}" if params else endpoint
        base_id = endpoint
    else:
        return AccessBlocker(
            query=q_clean,
            coverage=Coverage(queried=[source_name], errored={source_name: "missing-endpoint"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url="",
            detail="REST manifest requires 'url_template' or 'endpoint'.",
        )

    spec_key = source_name if get_spec(source_name) is not None else SOURCE
    _, default_ns, default_cav = spec_bounds(spec_key)
    eff_corpus = corpus or f"JSON REST endpoint {base_id}"
    eff_ns = list(not_searched) if not_searched is not None else default_ns
    eff_cav = list(caveats) if caveats is not None else default_cav
    req_headers = {"Accept": "application/json", **(headers or {})}

    return run_source(
        q_clean,
        source=source_name,
        url=url,
        parse=lambda body: _parse_rest(
            body,
            query_url=url,
            source_name=source_name,
            rows_path=rows_path,
            total_path=total_path,
            url_field=url_field,
            item_url_template=item_url_template,
            title_fields=title_fields,
            snippet_fields=snippet_fields,
            store=store,
        ),
        searched=f"{source_name} ({base_id}) for {q_clean!r}",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers=req_headers,
        cache_params={"endpoint": base_id, "rows_path": rows_path, "limit": cap},
        exact_match_supported=exact_match_supported,
        corpus=eff_corpus,
        not_searched=eff_ns,
        caveats=eff_cav,
    )


def run_manifest(
    manifest: dict,
    text_query: str = "",
    *,
    where: str | None = None,
    limit: int = 20,
    store: Store | None = None,
    limiter: Limiter | None = None,
    use_cache: bool = True,
):
    """Execute a declarative JSON REST manifest plugin."""
    q = (text_query or where or "").strip()
    return query(
        q,
        source_name=str(manifest.get("name") or SOURCE),
        endpoint=str(manifest.get("endpoint") or ""),
        url_template=manifest.get("url_template"),
        query_param=str(manifest.get("query_param") or "q"),
        limit_param=str(manifest.get("limit_param") or "rows"),
        static_params=manifest.get("static_params"),
        rows_path=str(manifest.get("rows_path") or "results"),
        total_path=manifest.get("total_path"),
        url_field=manifest.get("url_field"),
        item_url_template=manifest.get("item_url_template"),
        title_fields=manifest.get("title_fields"),
        snippet_fields=manifest.get("snippet_fields"),
        corpus=manifest.get("corpus"),
        not_searched=manifest.get("not_searched"),
        caveats=manifest.get("caveats"),
        exact_match_supported=manifest.get("exact_match_supported", False),
        headers=manifest.get("headers"),
        limit=limit,
        store=store,
        limiter=limiter,
        use_cache=use_cache,
    )
