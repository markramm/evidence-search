"""Four-state data-broker registries client (`evidence-search brokers`, Issue #20).

Covers state regulator lists OF data brokers (California CPPA, Vermont SOS,
Texas SOS, and Oregon DCBS) with DBA / legal-suffix normalization and explicit
registry snapshot dating on `VerifiedAbsence`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3

from ..core.bulk import bulk_root, check_bulk_cache, finalize_bulk_query, open_index_db
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, RateLimited, Result
from ..core.store import Store

SOURCE = "brokers"
STATE_FEEDS = {
    "CA": "https://cppa.ca.gov/data_broker_registry/data_brokers.csv",
    "VT": "https://sos.vermont.gov/media/databrokers.csv",
    "TX": "https://www.sos.state.tx.us/statdoc/data-brokers.csv",
    "OR": "https://dfr.oregon.gov/business/reg/Documents/data-brokers.csv",
}

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:inc|incorporated|llc|l\.l\.c|corp|corporation|ltd|limited|lp|llp|co|company)\b\.?",
    re.I,
)

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="California CPPA, Vermont SOS, Texas SOS, and Oregon DCBS data-broker registries.",
    not_searched=(
        "Data brokers exempt under state statutory thresholds (e.g. direct-relationship or revenue/record floors)",
        "States without a statutory data-broker registry",
    ),
    caveats=(
        "Data brokers frequently register under a holding company and operate consumer-facing sites under DBAs; check both legal names and DBA domains.",
    ),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_brokers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    broker_name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    dba_names TEXT,
    dba_norm TEXT,
    reg_number TEXT,
    reg_date TEXT,
    website TEXT,
    address TEXT
);
CREATE INDEX IF NOT EXISTS idx_brokers_state ON data_brokers(state);
CREATE INDEX IF NOT EXISTS idx_brokers_norm ON data_brokers(name_norm);
"""


def normalize_broker_name(name: str) -> str:
    s = _LEGAL_SUFFIX_RE.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def parse_brokers_payload(raw: str | bytes, default_state: str = "CA") -> list[dict]:
    """Parse a CSV or JSON data-broker registry export."""
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else (raw or "")
    text = text.strip()
    if not text:
        raise ValueError("empty data-brokers payload")

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid data-brokers JSON: {e}") from e
        if not isinstance(data, dict) or "brokers" not in data or not isinstance(data["brokers"], list):
            raise ValueError("data-brokers JSON missing `brokers` list")
        out: list[dict] = []
        for row in data["brokers"]:
            if not isinstance(row, dict):
                continue
            nm = str(row.get("broker_name") or row.get("name") or "").strip()
            dba = str(row.get("dba_names") or row.get("dba") or "").strip()
            if nm:
                out.append({
                    "state": str(row.get("state") or default_state).upper(),
                    "broker_name": nm,
                    "name_norm": normalize_broker_name(nm),
                    "dba_names": dba,
                    "dba_norm": normalize_broker_name(dba),
                    "reg_number": str(row.get("reg_number") or ""),
                    "reg_date": str(row.get("reg_date") or ""),
                    "website": str(row.get("website") or ""),
                    "address": str(row.get("address") or ""),
                })
        return out

    reader = csv.DictReader(io.StringIO(text))
    headers = {re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_"): h for h in (reader.fieldnames or []) if h}
    name_col = next((headers[k] for k in ("broker_name", "business_name", "name", "organization_name", "legal_name") if k in headers), None)
    if not name_col:
        raise ValueError(f"Unrecognized data-broker CSV headers: {reader.fieldnames}")
    dba_col = next((headers[k] for k in ("dba", "dba_names", "doing_business_as", "trade_name") if k in headers), None)
    reg_col = next((headers[k] for k in ("registration_number", "reg_number", "id", "filing_number") if k in headers), None)
    dt_col = next((headers[k] for k in ("registration_date", "reg_date", "effective_date", "date_registered") if k in headers), None)
    web_col = next((headers[k] for k in ("website", "url", "domain", "website_url") if k in headers), None)
    st_col = next((headers[k] for k in ("state", "registry_state") if k in headers), None)
    addr_col = next((headers[k] for k in ("address", "mailing_address", "primary_address") if k in headers), None)

    out = []
    for row in reader:
        nm = str(row.get(name_col) or "").strip()
        if not nm:
            continue
        dba = str(row.get(dba_col) or "").strip() if dba_col else ""
        out.append({
            "state": str(row.get(st_col) or default_state).upper()[:2] if st_col else default_state,
            "broker_name": nm,
            "name_norm": normalize_broker_name(nm),
            "dba_names": dba,
            "dba_norm": normalize_broker_name(dba),
            "reg_number": str(row.get(reg_col) or "").strip() if reg_col else "",
            "reg_date": str(row.get(dt_col) or "").strip() if dt_col else "",
            "website": str(row.get(web_col) or "").strip() if web_col else "",
            "address": str(row.get(addr_col) or "").strip() if addr_col else "",
        })
    return out


def conn_for(cache_dir: Path | None = None) -> sqlite3.Connection:
    return open_index_db(cache_dir or bulk_root(SOURCE, "EVIDENCE_BROKERS_DIR"), SCHEMA)


def index_brokers(conn: sqlite3.Connection, rows: list[dict], *, state: str | None = None) -> int:
    with conn:
        if state:
            conn.execute("DELETE FROM data_brokers WHERE state = ?", (state.upper(),))
        else:
            conn.execute("DELETE FROM data_brokers")
        conn.executemany(
            "INSERT INTO data_brokers "
            "(state, broker_name, name_norm, dba_names, dba_norm, reg_number, reg_date, website, address) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (r["state"], r["broker_name"], r["name_norm"], r["dba_names"],
                 r["dba_norm"], r["reg_number"], r["reg_date"], r["website"], r["address"])
                for r in rows
            ],
        )
    return len(rows)


def search(query: str, *, state: str = "all", exact_only: bool = False,
           refresh: bool = False, limit: int = 25, store: Store | None = None,
           limiter: Limiter | None = None, cache_dir: Path | None = None,
           use_cache: bool = True):
    """Search state data-broker registrations (CA, VT, TX, OR)."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (query or "").strip()
    q_norm = normalize_broker_name(q_clean)
    if not q_norm:
        return AccessBlocker(
            query=q_clean,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "empty-query"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=STATE_FEEDS["CA"],
            detail="brokers search requires an entity name or domain.",
        )

    st_filter = (state or "all").strip().upper()
    searched = f"State data-broker registries ({st_filter}: {q_clean!r}, norm={q_norm!r})"
    cache_params = {
        "state": st_filter,
        "exact_only": exact_only,
        "limit": limit,
        "cache_dir": str(cache_dir or ""),
    }
    if (
        not refresh
        and (cached := check_bulk_cache(
            q_clean,
            source=SOURCE,
            searched=searched,
            store=store,
            use_cache=use_cache,
            cache_params=cache_params,
        )) is not None
    ):
        return cached

    conn = conn_for(cache_dir)
    try:
        target_states = tuple(STATE_FEEDS) if st_filter == "ALL" else (st_filter,)
        for st_code in target_states:
            if st_code not in STATE_FEEDS:
                continue
            st_row = conn.execute(
                "SELECT COUNT(*) FROM data_brokers WHERE state = ?",
                (st_code,),
            ).fetchone()
            st_has_rows = bool(st_row and st_row[0] > 0)
            if st_has_rows and not refresh:
                continue
            allowed, retry, why = limiter.reserve(SOURCE)
            if not allowed:
                return RateLimited(
                    query=q_clean,
                    coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                    source=SOURCE,
                    retry_after_s=int(retry) if retry else None,
                    detail=why,
                )
            body, err = fetch(STATE_FEEDS[st_code], source=SOURCE, query=f"{st_code}:{q_clean}", timeout=30.0)
            limiter.note_outcome(SOURCE, err)
            if err:
                return err
            try:
                rows = parse_brokers_payload(body or "", default_state=st_code)
                if rows:
                    index_brokers(conn, rows, state=st_code)
            except ValueError as e:
                return AccessBlocker(
                    query=q_clean,
                    coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse-failed"}),
                    mechanism=Blocker.SERVER_ERROR,
                    url=STATE_FEEDS[st_code],
                    detail=f"{e} -- NOT an absence.",
                )

        if exact_only:
            clauses = ["(name_norm = ? OR dba_norm = ?)"]
            sql_params: list[str] = [q_norm, q_norm]
        else:
            pat = f"%{q_norm}%"
            raw_pat = f"%{q_clean.lower()}%"
            clauses = ["(name_norm LIKE ? OR dba_norm LIKE ? OR LOWER(website) LIKE ?)"]
            sql_params = [pat, pat, raw_pat]

        if st_filter != "ALL":
            clauses.append("state = ?")
            sql_params.append(st_filter)

        where_sql = " AND ".join(clauses)
        matched = conn.execute(
            f"SELECT state, broker_name, dba_names, reg_number, reg_date, website, address "
            f"FROM data_brokers WHERE {where_sql} ORDER BY state, broker_name LIMIT ?",
            (*sql_params, max(1, limit)),
        ).fetchall()
        tot_row = conn.execute(f"SELECT COUNT(*) FROM data_brokers WHERE {where_sql}", tuple(sql_params)).fetchone()
        total_matches = int(tot_row[0]) if tot_row else len(matched)
    finally:
        conn.close()

    probe_params = {"normalized_variant": q_norm, "state": st_filter, "exact_only": exact_only}
    endpoint = STATE_FEEDS.get(st_filter, STATE_FEEDS["CA"])

    results: list[Result] = []
    for st_c, nm, dba, reg_num, reg_dt, web, addr in matched:
        reg_key = reg_num or hashlib.sha256(nm.encode()).hexdigest()[:8]
        rid = f"{SOURCE}:{st_c}:{reg_key}"
        rec_payload = {
            "state": st_c, "broker_name": nm, "dba_names": dba,
            "reg_number": reg_num, "reg_date": reg_dt,
            "website": web, "address": addr,
        }
        try:
            store.put_record(rid, SOURCE, q_clean, rec_payload)
        except Exception:
            pass
        url = web or STATE_FEEDS.get(st_c, STATE_FEEDS["CA"])
        title = f"[{st_c}] {nm}" + (f" (DBA: {dba})" if dba else "")
        snippet = " | ".join(p for p in (
            f"reg: {reg_num}" if reg_num else "",
            f"date: {reg_dt}" if reg_dt else "",
            f"website: {web}" if web else "",
            addr,
        ) if p)
        results.append(Result(
            url=url,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[f"{SOURCE}:{st_c.lower()}"],
            index_origin=["own-index"],
            meta={**rec_payload, "record_id": rid, "total_matches": total_matches},
        ))
    return finalize_bulk_query(
        q_clean,
        source=SOURCE,
        endpoint=endpoint,
        searched=searched,
        results=results,
        probe_params=probe_params,
        corpus=CORPUS,
        not_searched=NOT_SEARCHED,
        caveats=STANDING_CAVEATS,
        store=store,
        use_cache=use_cache,
        exact_match_supported=True,
        cache_params=cache_params,
    )
