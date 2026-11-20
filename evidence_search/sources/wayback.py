"""Internet Archive Wayback Machine CDX client (`evidence-search wayback`).

Queries the Wayback Machine CDX Server JSON API:
  `https://web.archive.org/cdx/search/cdx?url=<target>&output=json&fl=timestamp,original,mimetype,statuscode,digest,length`

Investigative use cases:
  * Provenance & deleted-page recovery: find when a contractor or agency posted,
    altered, or removed a webpage, press release, or PDF.
  * Content-change tracking via SHA-1 base32 `digest`: consecutive captures with
    different `digest` values pinpoint the exact window when page text changed.
  * Every result includes both the standard Wayback URL and `raw_archive_url`
    (`https://web.archive.org/web/<timestamp>id_/<original>`), which serves the
    original capture bytes without Wayback's HTML toolbar so it can be piped
    straight into `evidence-search extract`.

Epistemic guarantees:
  * CDX `output=json` returns a JSON array-of-arrays (`[["timestamp", ...], ...]`)
    when captures exist and an empty JSON array `[]` when zero captures exist.
  * Any non-list JSON (`{}`) or non-JSON body raises `ValueError` ->
    `AccessBlocker(PARSE_FAILED)`, never a false `VerifiedAbsence`.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import Result
from ..core.source import RecognizedEmpty, run_source
from ..core.store import Store

SOURCE = "wayback"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
AVAILABLE_URL = "https://archive.org/wayback/available"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="Internet Archive Wayback Machine CDX index (web.archive.org/cdx/search/cdx).",
    not_searched=(
        "Live web pages never crawled by the Internet Archive or excluded via robots.txt / takedown request",
        "Other web archives (archive.today, Perma.cc, Library of Congress web archives)",
        "Full text inside archived captures (pass `raw_archive_url` to `evidence-search extract`)",
    ),
    caveats=(
        "CDX queries match exact URLs by default (`--match-type exact`); pass `--match-type prefix` or `--match-type domain` to sweep subpaths.",
    ),
)


def _fmt_ts(ts: str) -> str:
    if len(ts) >= 8 and ts[:8].isdigit():
        d = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        if len(ts) >= 14 and ts[8:14].isdigit():
            return f"{d} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z"
        return d
    return ts


def _parse_cdx(body: str, target_url: str, verify_empty_fn=None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"wayback CDX returned non-JSON: {e}") from e

    # CDX output=json is ALWAYS a JSON list (`[header, row1, ...]`).
    # Reject `{}` or any non-list so malformed responses never certify an absence!
    if not isinstance(data, list):
        raise ValueError(f"wayback CDX expected JSON array, got {type(data).__name__}")
    if len(data) == 0:
        if verify_empty_fn is not None and verify_empty_fn():
            return RecognizedEmpty("archived_snapshots: {}")
        raise ValueError("wayback CDX returned bare [] without a verified header or availability envelope")

    header = data[0]
    if not isinstance(header, list) or "timestamp" not in header or "original" not in header:
        raise ValueError(f"wayback CDX unexpected header row: {header!r}")
    if len(data) == 1:
        return RecognizedEmpty("cdx header with 0 capture rows")

    rows = [dict(zip(header, r)) for r in data[1:] if isinstance(r, list) and len(r) == len(header)]
    if not rows:
        return RecognizedEmpty("cdx header with 0 capture rows")

    distinct_digests = sorted({r.get("digest", "") for r in rows if r.get("digest")})
    total = len(rows)
    out: list[Result] = []
    for r in rows:
        ts = str(r.get("timestamp") or "")
        orig = str(r.get("original") or target_url)
        mime = str(r.get("mimetype") or "")
        status = str(r.get("statuscode") or "")
        digest = str(r.get("digest") or "")
        length = str(r.get("length") or "")
        wb_url = f"https://web.archive.org/web/{ts}/{orig}"
        raw_wb_url = f"https://web.archive.org/web/{ts}id_/{orig}"
        iso_dt = _fmt_ts(ts)
        title = f"[{iso_dt}] {orig} (HTTP {status or '?'}, {mime or '?'})"
        snippet = (
            f"digest: {digest[:16]}... | bytes: {length} | "
            f"distinct versions in result set: {len(distinct_digests)} | "
            f"extract URL: {raw_wb_url}"
        )
        out.append(Result(
            url=wb_url,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[SOURCE],
            meta={
                "timestamp": ts,
                "captured_at": iso_dt,
                "original_url": orig,
                "mimetype": mime,
                "statuscode": status,
                "digest": digest,
                "length": int(length) if length.isdigit() else length,
                "archive_url": wb_url,
                "raw_archive_url": raw_wb_url,
                "distinct_digest_count": len(distinct_digests),
                "total_matches": total,
            },
        ))
    return out


def search(target_url: str, *, match_type: str = "exact",
           date_from: str | None = None, date_to: str | None = None,
           status_code: str | None = None, collapse_digest: bool = False,
           limit: int = 25, store: Store | None = None,
           limiter: Limiter | None = None, use_cache: bool = True):
    """Query the Wayback Machine CDX API for historical captures of `target_url`."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    clean_url = (target_url or "").strip()
    params: dict[str, str] = {
        "url": clean_url,
        "output": "json",
        "fl": "timestamp,original,mimetype,statuscode,digest,length",
        "limit": str(-max(1, min(limit, 500))),  # negative limit returns most recent N captures
    }
    if match_type and match_type != "exact":
        params["matchType"] = match_type
    if date_from:
        params["from"] = date_from.replace("-", "")
    if date_to:
        params["to"] = date_to.replace("-", "")
    if status_code:
        params["filter"] = f"statuscode:{status_code}"
    if collapse_digest:
        params["collapse"] = "digest"

    url = f"{CDX_URL}?{urlencode(params)}"
    label = clean_url + (f" ({match_type})" if match_type != "exact" else "")

    def _verify_empty() -> bool:
        av_url = f"{AVAILABLE_URL}?{urlencode({'url': clean_url})}"
        av_body, av_err = fetch(av_url, source=SOURCE, query=clean_url,
                                headers={"Accept": "application/json"})
        if av_err or not av_body:
            return False
        try:
            av_data = json.loads(av_body)
        except json.JSONDecodeError:
            return False
        return (
            isinstance(av_data, dict)
            and isinstance(av_data.get("archived_snapshots"), dict)
            and not av_data["archived_snapshots"]
        )

    return run_source(
        label,
        source=SOURCE,
        url=url,
        parse=lambda body: _parse_cdx(body, clean_url, verify_empty_fn=_verify_empty),
        searched=f"Wayback Machine CDX ({label})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params=params,
        exact_match_supported=(match_type in ("exact", "prefix", "host", "domain")),
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
