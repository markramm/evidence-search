"""Generic Socrata / Tyler Data & Insights SODA v2 driver (`evidence-search socrata`).

Hundreds of state checkbooks, municipal contract portals (NYC Checkbook/PASSPort,
Chicago, Los Angeles, Texas Comptroller, Washington, Illinois, California),
police-misconduct registries, and local campaign-finance tables run on Socrata
(`/resource/<4x4-dataset-id>.json`).

This driver turns any Socrata dataset into a typed-outcome source with atomic
rate limiting, caching, and publishable `VerifiedAbsence` semantics — and also
backs declarative JSON manifest plugins (`"driver": "socrata"`) in
`~/.evidence-search/plugins/*.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import urlencode

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import get_spec, spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, run_source
from ..core.store import Store

SOURCE = "socrata"


def _row_title(row: dict, title_fields: list[str] | None = None) -> str:
    if title_fields:
        parts = [str(row.get(f) or "").strip() for f in title_fields if row.get(f) not in (None, "")]
        if parts:
            return " — ".join(parts)[:180]
    # Pick the first few informative scalar fields
    skip = {k for k in row if k.startswith(":")}
    parts = []
    for k, v in row.items():
        if k in skip or isinstance(v, (dict, list)) or v in (None, ""):
            continue
        parts.append(f"{k}={v}")
        if len(parts) >= 4:
            break
    return " | ".join(parts)[:180] or "Socrata record"


def _parse_socrata(body: str, *, domain: str, dataset_id: str, portal_url: str,
                   source_name: str, title_fields: list[str] | None = None,
                   verify_empty_fn=None, store: Store | None = None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"socrata returned non-JSON: {e}") from e

    # Socrata returns a top-level JSON list `[...]` on success, and a JSON dict
    # `{"error": true, "message": ...}` or `{"code": ...}` on error. Reject any
    # dict so an error object or `{}` can never certify a false absence!
    if isinstance(data, dict):
        msg = data.get("message") or data.get("error") or data.get("code") or "unexpected JSON object"
        raise ValueError(f"socrata returned object instead of row list: {msg}")
    if not isinstance(data, list):
        raise ValueError(f"socrata returned unexpected JSON type {type(data).__name__}")
    if not data:
        # A bare `[]` has no schema envelope of its own. Confirm against
        # Socrata's `SELECT count(*) AS count` (`[{"count": "0"}]`) before
        # certifying an absence.
        if verify_empty_fn is not None and verify_empty_fn():
            return RecognizedEmpty("count: 0")
        raise ValueError("socrata returned bare [] without a verified count=0 envelope")
    if len(data) == 1 and isinstance(data[0], dict) and str(data[0].get("count", "")) == "0" and len(data[0]) == 1:
        return RecognizedEmpty("count: 0")

    out: list[Result] = []
    total = len(data)
    for row in data:
        if not isinstance(row, dict):
            continue
        title = _row_title(row, title_fields)
        snippet_parts = []
        for k, v in row.items():
            if k.startswith(":") or isinstance(v, (dict, list)) or v in (None, ""):
                continue
            snippet_parts.append(f"{k}: {v}")
            if len(snippet_parts) >= 6:
                break
        row_key = str(row.get(":id") or row.get("id") or row.get("record_id") or "").strip()
        if not row_key:
            row_key = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:12]
        rid = f"{source_name}:{dataset_id}:{row_key}"
        if store is not None:
            try:
                store.put_record(rid, source_name, portal_url, row)
            except Exception:
                pass
        out.append(Result(
            url=portal_url,
            title=title,
            snippet=" | ".join(snippet_parts)[:360],
            source=source_name,
            engines=[f"socrata:{domain}/{dataset_id}"],
            meta={
                "record_id": rid,
                "domain": domain,
                "dataset_id": dataset_id,
                "row": row,
                "total_matches": total,
            },
        ))
    if not out:
        raise ValueError("socrata row list contained no JSON objects")
    return out


def query(domain: str, dataset_id: str, text_query: str = "", *,
          where: str | None = None, select: str | None = None,
          order: str | None = None, limit: int = 20,
          source_name: str = SOURCE, corpus: str | None = None,
          not_searched: list[str] | None = None, caveats: list[str] | None = None,
          title_fields: list[str] | None = None,
          store: Store | None = None, limiter: Limiter | None = None,
          use_cache: bool = True):
    """Query a Socrata SODA v2 dataset (`https://<domain>/resource/<dataset_id>.json`)."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    clean_domain = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    clean_dataset = (dataset_id or "").strip().removesuffix(".json")
    if not clean_domain or not clean_dataset:
        return AccessBlocker(
            query=text_query or where or "",
            coverage=Coverage(queried=[source_name], errored={source_name: "missing-target"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url="https://dev.socrata.com/",
            detail="socrata requires both `--domain <host>` and `--dataset <4x4-id>`.",
        )

    params: dict[str, str] = {"$limit": str(max(1, min(limit, 1000)))}
    q_clean = (text_query or "").strip()
    where_clean = (where or "").strip()
    if q_clean:
        params["$q"] = q_clean
    if where_clean:
        params["$where"] = where_clean
    if select:
        params["$select"] = select.strip()
    if order:
        params["$order"] = order.strip()

    endpoint = f"https://{clean_domain}/resource/{clean_dataset}.json"
    url = f"{endpoint}?{urlencode(params)}"
    portal_url = f"https://{clean_domain}/d/{clean_dataset}"

    headers = {"Accept": "application/json"}
    token = (os.environ.get("SOCRATA_APP_TOKEN") or "").strip()
    if token:
        headers["X-App-Token"] = token

    spec_key = source_name if get_spec(source_name) is not None else SOURCE
    _, default_ns, default_cav = spec_bounds(spec_key)
    eff_corpus = corpus or (
        f"Socrata dataset {clean_dataset} on {clean_domain} ({endpoint})."
    )
    eff_not_searched = list(not_searched) if not_searched is not None else default_ns
    eff_caveats = list(caveats) if caveats is not None else default_cav
    exact = bool(where_clean and not q_clean)
    label = f"{clean_domain}/{clean_dataset}: " + (
        f"$where={where_clean}" if where_clean else f"$q={q_clean or '*'}"
    )

    def _verify_empty() -> bool:
        cnt_params = {k: v for k, v in params.items() if k not in ("$limit", "$order")}
        cnt_params["$select"] = "count(*) as count"
        cnt_url = f"{endpoint}?{urlencode(cnt_params)}"
        c_body, c_err = fetch(cnt_url, source=source_name, query=label, headers=headers)
        if c_err or not c_body:
            return False
        try:
            c_data = json.loads(c_body)
        except json.JSONDecodeError:
            return False
        return (
            isinstance(c_data, list)
            and len(c_data) == 1
            and isinstance(c_data[0], dict)
            and str(c_data[0].get("count", "")) == "0"
        )

    return run_source(
        label,
        source=source_name,
        url=url,
        parse=lambda body: _parse_socrata(
            body, domain=clean_domain, dataset_id=clean_dataset,
            portal_url=portal_url, source_name=source_name,
            title_fields=title_fields, verify_empty_fn=_verify_empty,
            store=store,
        ),
        searched=f"Socrata {label}",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers=headers,
        cache_params={"domain": clean_domain, "dataset": clean_dataset, "where": where_clean, "limit": limit},
        exact_match_supported=exact,
        corpus=eff_corpus,
        not_searched=eff_not_searched,
        caveats=eff_caveats,
    )


def run_manifest(manifest: dict, text_query: str = "", *, where: str | None = None,
                 limit: int = 20, store: Store | None = None,
                 limiter: Limiter | None = None, use_cache: bool = True):
    """Execute a declarative Socrata JSON manifest plugin."""
    q = (text_query or "").strip()
    eff_where = (where or "").strip()
    default_field = (manifest.get("search_field") or "").strip()
    if q and default_field and not eff_where:
        escaped = q.replace("'", "''").upper()
        eff_where = f"upper({default_field}) LIKE '%{escaped}%'"
        q_arg = ""
    else:
        q_arg = q
    return query(
        domain=str(manifest["domain"]),
        dataset_id=str(manifest["dataset_id"]),
        text_query=q_arg,
        where=eff_where or None,
        select=manifest.get("select"),
        order=manifest.get("order"),
        limit=limit,
        source_name=str(manifest.get("name") or SOURCE),
        corpus=manifest.get("corpus"),
        not_searched=manifest.get("not_searched"),
        caveats=manifest.get("caveats"),
        title_fields=manifest.get("title_fields"),
        store=store,
        limiter=limiter,
        use_cache=use_cache,
    )
