"""Certificate Transparency (crt.sh) log search (`evidence-search crtsh`).

Queries `https://crt.sh/?q=<domain>&output=json` to enumerate TLS certificates
logged across public CT logs for a domain or wildcard (`%.vendor.com`).

Investigative use cases:
  * Discover hidden staging subdomains, agency-branded portals, and shared SAN
    certificates linking ostensibly separate LLCs without touching the target's
    infrastructure.
  * When `crt.sh` returns a bare `[]` (empty array), verifies against a known
    control cert lookup (`?id=1&output=json`) before certifying a `VerifiedAbsence`,
    so a malformed or empty-slice failure can never masquerade as an absence.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "crtsh"
CRTSH_URL = "https://crt.sh/"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="crt.sh public Certificate Transparency logs (X.509 certificates).",
    not_searched=(
        "Self-signed internal certificates or private CAs never submitted to public CT logs",
        "Unencrypted HTTP-only hosts with no TLS certificates issued",
        "DNS records without associated TLS certificate issuance",
    ),
    caveats=(
        "Pass a wildcard prefix (`%.example.gov`) to include subdomains; expired certificates remain in CT logs unless excluded with `--active-only`.",
    ),
)


def _parse_crtsh(body: str, domain: str, *, include_expired: bool = True,
                 limit: int = 50, verify_empty_fn=None,
                 store: Store | None = None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"crt.sh returned non-JSON: {e}") from e

    if isinstance(data, dict) and "certificates" in data:
        rows = json_rows(data, "certificates", total_key=None)
        if isinstance(rows, RecognizedEmpty):
            return rows
    elif isinstance(data, list):
        if len(data) == 0:
            if verify_empty_fn is not None and verify_empty_fn():
                return RecognizedEmpty("crt.sh: [] verified")
            raise ValueError("crt.sh returned bare [] without availability verification")
        rows = data
    else:
        raise ValueError(f"crt.sh returned unexpected JSON type {type(data).__name__}")

    valid_rows = [
        r for r in rows
        if isinstance(r, dict) and ("common_name" in r or "name_value" in r or "id" in r)
    ]
    if not valid_rows:
        raise ValueError("crt.sh rows missing expected certificate fields (common_name / name_value / id)")

    seen_ids: set[str] = set()
    out: list[Result] = []
    all_sans: set[str] = set()
    for r in valid_rows:
        cid = str(r.get("id") or r.get("min_cert_id") or "").strip()
        if cid and cid in seen_ids:
            continue
        if cid:
            seen_ids.add(cid)
        cn = str(r.get("common_name") or "").strip()
        raw_names = str(r.get("name_value") or cn).strip()
        sans = [n.strip() for n in raw_names.splitlines() if n.strip()]
        for s in sans:
            all_sans.add(s)
        issuer = str(r.get("issuer_name") or "").strip()
        nb = str(r.get("not_before") or "")[:10]
        na = str(r.get("not_after") or "")[:10]
        serial = str(r.get("serial_number") or "").strip()
        rid = f"{SOURCE}:{cid or hashlib.sha256(raw_names.encode()).hexdigest()[:12]}"
        if store is not None:
            try:
                store.put_record(rid, SOURCE, domain, r)
            except Exception:
                pass
        out.append(Result(
            url=f"https://crt.sh/?id={cid}" if cid else f"https://crt.sh/?q={domain}",
            title=f"[{nb or '?'} -> {na or '?'}] {cn or (sans[0] if sans else domain)}",
            snippet=f"SANs ({len(sans)}): {', '.join(sans[:4])} | issuer: {issuer[:120]}",
            source=SOURCE,
            engines=[SOURCE],
            meta={
                "record_id": rid,
                "cert_id": cid,
                "common_name": cn,
                "san_domains": sans,
                "issuer_name": issuer,
                "not_before": nb,
                "not_after": na,
                "serial_number": serial,
                "total_matches": len(valid_rows),
            },
        ))
        if len(out) >= max(1, limit):
            break

    if out:
        out[0].meta["unique_san_count"] = len(all_sans)
    return out


def search(domain: str, *, active_only: bool = False, limit: int = 50,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Query crt.sh Certificate Transparency logs for `domain` (or `%.domain`)."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (domain or "").strip()
    if not q_clean:
        return AccessBlocker(
            query="",
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "empty-domain"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=CRTSH_URL,
            detail="crtsh requires a domain or wildcard (e.g. `example.gov` or `%.example.gov`).",
        )

    params: dict[str, str] = {"q": q_clean, "output": "json"}
    if active_only:
        params["exclude"] = "expired"
    url = f"{CRTSH_URL}?{urlencode(params)}"

    def _verify_empty() -> bool:
        ctrl_url = f"{CRTSH_URL}?{urlencode({'id': '1', 'output': 'json'})}"
        c_body, c_err = fetch(ctrl_url, source=SOURCE, query="control:id=1",
                              headers={"Accept": "application/json"})
        if c_err or not c_body:
            return False
        try:
            c_data = json.loads(c_body)
        except json.JSONDecodeError:
            return False
        if isinstance(c_data, dict) and c_data.get("id"):
            return True
        return isinstance(c_data, list) and len(c_data) > 0 and isinstance(c_data[0], dict) and "id" in c_data[0]

    return run_source(
        q_clean,
        source=SOURCE,
        url=url,
        parse=lambda body: _parse_crtsh(
            body, q_clean, include_expired=not active_only,
            limit=limit, verify_empty_fn=_verify_empty, store=store,
        ),
        searched=f"crt.sh Certificate Transparency ({q_clean})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params={"q": q_clean, "active_only": active_only, "limit": limit},
        exact_match_supported=True,
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
