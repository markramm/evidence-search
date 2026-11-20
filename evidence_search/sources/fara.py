"""U.S. DOJ Foreign Agents Registration Act (FARA) client (`evidence-search fara`).

The sibling to Senate LDA (`lda.py`): domestic lobbying goes to Senate LDA,
while work on behalf of foreign governments, foreign political parties, and
foreign principals goes to DOJ FARA (`efile.fara.gov`).

Supports both:
  1. FARA's daily bulk ZIP/CSV datasets (`FARA_All_ForeignPrincipals.csv.zip`,
     `FARA_All_Registrants.csv.zip`) indexed into a local SQLite database via
     `core/bulk.py`, and
  2. Direct parsing of FARA API JSON (`{"FOREIGN_PRINCIPALS": {"ROW": [...]}}`,
     `{"REGISTRANTS": {"ROW": [...]}}`) or FARA CSV payloads when fetched over
     HTTP.

Epistemic guarantees:
  * Rejects `{}` or non-FARA payloads with `ValueError` -> `AccessBlocker(PARSE_FAILED)`
    so a soft WAF block or malformed response can NEVER certify a false absence.
  * Every `VerifiedAbsence` explicitly lists domestic Senate LDA filings,
    unregistered covert foreign influence, and 22 U.S.C. § 613 commercial
    exemptions under `not_searched`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import zipfile

from ..core.bulk import (
    bulk_root,
    check_bulk_cache,
    download_bulk_file,
    finalize_bulk_query,
    open_index_db,
)
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, RateLimited, Result
from ..core.source import RecognizedEmpty
from ..core.store import Store

SOURCE = "fara"
FARA_PRINCIPALS_URL = "https://efile.fara.gov/bulk/zip/FARA_All_ForeignPrincipals.csv.zip"
FARA_REGISTRANTS_URL = "https://efile.fara.gov/bulk/zip/FARA_All_Registrants.csv.zip"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="U.S. Department of Justice FARA eFile registry of active and terminated foreign principals and registrants.",
    not_searched=(
        "Domestic lobbying registrations under the Lobbying Disclosure Act (use `evidence-search lda`)",
        "Unregistered foreign lobbying or covert influence operations not filed with DOJ FARA",
        "Commercial/diplomatic activities exempt from FARA under 22 U.S.C. § 613",
    ),
    caveats=(
        "Foreign principals and registrants may be indexed under formal ministry names, embassy names, or corporate acronyms; check country and name variants.",
    ),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fara_principals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reg_number TEXT NOT NULL,
    principal_name TEXT NOT NULL,
    country TEXT,
    principal_reg_date TEXT,
    terminate_date TEXT,
    registrant_name TEXT,
    registrant_date TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_fara_reg ON fara_principals(reg_number);
CREATE INDEX IF NOT EXISTS idx_fara_country ON fara_principals(country);
"""


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (k or "").strip().lower()).strip("_")


def _extract_csv_text(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        if raw[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not names:
                    raise ValueError("FARA zip archive contained no .csv file")
                data = zf.read(names[0])
                try:
                    return data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    return data.decode("iso-8859-1", errors="replace")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw.decode("iso-8859-1", errors="replace")
    return raw


def parse_fara_payload(raw: str | bytes) -> list[dict]:
    """Parse a FARA CSV (or ZIP-wrapped CSV) or FARA `/api/v1/.../json` payload.

    Raises `ValueError` if the payload is neither a recognized FARA JSON
    envelope nor a CSV with FARA headers (`Registration_Number` /
    `Foreign_Principal` / `Registrant_Name`). Raises `RecognizedEmpty` if the
    envelope is valid but contains zero rows.
    """
    text = _extract_csv_text(raw).strip()
    if not text:
        raise ValueError("empty FARA payload")

    # 1. Check JSON format first (`{"FOREIGN_PRINCIPALS": {"ROW": [...]}}` or `{"REGISTRANTS": ...}`)
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid FARA JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("FARA JSON response is not an object")
        container = None
        for key in ("FOREIGN_PRINCIPALS", "REGISTRANTS", "SHORT_FORMS", "DOCS"):
            if key in data and isinstance(data[key], dict):
                container = data[key]
                break
        if container is None:
            raise ValueError("FARA JSON missing expected root key (FOREIGN_PRINCIPALS / REGISTRANTS)")
        rows_raw = container.get("ROW", [])
        if isinstance(rows_raw, dict):
            rows_raw = [rows_raw]
        if not isinstance(rows_raw, list):
            raise ValueError("FARA JSON ROW is not a list")
        if not rows_raw:
            return RecognizedEmpty("FARA JSON ROW: []")
        rows = []
        for r in rows_raw:
            if not isinstance(r, dict):
                continue
            nk = {_norm_key(k): str(v or "").strip() for k, v in r.items()}
            rows.append(_normalize_fara_row(nk))
        return rows

    # 2. CSV format: verify header row has FARA columns
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("FARA CSV has no header row")
    norm_headers = {_norm_key(h) for h in reader.fieldnames if h}
    required_any = {"registration_number", "reg_number", "foreign_principal", "registrant_name"}
    if not (norm_headers & required_any):
        raise ValueError(f"Unrecognized FARA CSV headers: {reader.fieldnames[:5]}")

    rows = []
    for raw_row in reader:
        nk = {_norm_key(k): str(v or "").strip() for k, v in raw_row.items() if k}
        normed = _normalize_fara_row(nk)
        if normed["reg_number"] or normed["principal_name"] or normed["registrant_name"]:
            rows.append(normed)
    if not rows:
        return RecognizedEmpty("FARA CSV header with 0 rows")
    return rows


def _normalize_fara_row(nk: dict[str, str]) -> dict:
    reg_num = nk.get("registration_number") or nk.get("reg_number") or ""
    principal = (
        nk.get("foreign_principal")
        or nk.get("foreign_principal_name")
        or nk.get("fp_name")
        or ""
    )
    country = (
        nk.get("country_location_represented")
        or nk.get("country_name")
        or nk.get("country")
        or ""
    )
    registrant = nk.get("registrant_name") or nk.get("name") or ""
    p_date = (
        nk.get("foreign_principal_registration_date")
        or nk.get("fp_reg_date")
        or nk.get("registration_date")
        or ""
    )
    term_date = (
        nk.get("foreign_principal_termination_date")
        or nk.get("termination_date")
        or nk.get("term_date")
        or ""
    )
    r_date = nk.get("registrant_date") or nk.get("registration_date") or ""
    addr = " ".join(p for p in (nk.get("address_1", ""), nk.get("address_2", "")) if p).strip()
    city = nk.get("city") or ""
    state = nk.get("state") or ""
    status = "Terminated" if term_date else (nk.get("status") or "Active")
    return {
        "reg_number": reg_num,
        "principal_name": principal,
        "country": country,
        "principal_reg_date": p_date,
        "terminate_date": term_date,
        "registrant_name": registrant,
        "registrant_date": r_date,
        "address": addr,
        "city": city,
        "state": state,
        "status": status,
    }


def conn_for(cache_dir: Path | None = None) -> sqlite3.Connection:
    root = cache_dir or bulk_root(SOURCE, "EVIDENCE_FARA_DIR")
    return open_index_db(root, SCHEMA)


def index_fara_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    with conn:
        conn.execute("DELETE FROM fara_principals")
        conn.executemany(
            "INSERT INTO fara_principals "
            "(reg_number, principal_name, country, principal_reg_date, terminate_date, "
            " registrant_name, registrant_date, address, city, state, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["reg_number"], r["principal_name"], r["country"],
                    r["principal_reg_date"], r["terminate_date"],
                    r["registrant_name"], r["registrant_date"],
                    r["address"], r["city"], r["state"], r["status"],
                )
                for r in rows
            ],
        )
    return len(rows)


def search(query: str = "", *, principal: str | None = None,
           registrant: str | None = None, country: str | None = None,
           reg_number: str | None = None, active_only: bool = False,
           refresh: bool = False, limit: int = 25, store: Store | None = None,
           limiter: Limiter | None = None, cache_dir: Path | None = None,
           use_cache: bool = True):
    """Search DOJ FARA foreign principals and registrants."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (query or "").strip()
    p_clean = (principal or "").strip()
    r_clean = (registrant or "").strip()
    c_clean = (country or "").strip()
    n_clean = (reg_number or "").strip()

    parts = []
    if q_clean:
        parts.append(q_clean)
    if p_clean:
        parts.append(f"principal={p_clean}")
    if r_clean:
        parts.append(f"registrant={r_clean}")
    if c_clean:
        parts.append(f"country={c_clean}")
    if n_clean:
        parts.append(f"reg_number={n_clean}")
    if active_only:
        parts.append("active_only=true")
    label = " ".join(parts) or "*"
    searched = f"DOJ FARA registry ({label})"
    cache_params = {
        "principal": p_clean,
        "registrant": r_clean,
        "country": c_clean,
        "reg_number": n_clean,
        "active_only": active_only,
        "limit": limit,
        "cache_dir": str(cache_dir or ""),
    }
    if (
        not refresh
        and (cached := check_bulk_cache(
            label,
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
        count_row = conn.execute("SELECT COUNT(*) FROM fara_principals").fetchone()
        has_local = bool(count_row and count_row[0] > 0)

        if refresh or not has_local:
            allowed, retry, why = limiter.reserve(SOURCE)
            if not allowed:
                return RateLimited(
                    query=label,
                    coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                    source=SOURCE,
                    retry_after_s=int(retry) if retry else None,
                    detail=why,
                )
            body, err = fetch(FARA_PRINCIPALS_URL, source=SOURCE, query=label, timeout=45.0)
            limiter.note_outcome(SOURCE, err)
            if err:
                return err
            try:
                rows = parse_fara_payload(body or "")
                if rows:
                    index_fara_rows(conn, rows)
            except ValueError as e:
                return AccessBlocker(
                    query=label,
                    coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse-failed"}),
                    mechanism=Blocker.SERVER_ERROR,
                    url=FARA_PRINCIPALS_URL,
                    detail=f"{e} -- NOT an absence.",
                )

        clauses = []
        sql_params: list[str] = []
        if q_clean:
            clauses.append("(UPPER(principal_name) LIKE ? OR UPPER(registrant_name) LIKE ? OR UPPER(country) LIKE ? OR reg_number = ?)")
            pat = f"%{q_clean.upper()}%"
            sql_params.extend([pat, pat, pat, q_clean])
        if p_clean:
            clauses.append("UPPER(principal_name) LIKE ?")
            sql_params.append(f"%{p_clean.upper()}%")
        if r_clean:
            clauses.append("UPPER(registrant_name) LIKE ?")
            sql_params.append(f"%{r_clean.upper()}%")
        if c_clean:
            clauses.append("UPPER(country) LIKE ?")
            sql_params.append(f"%{c_clean.upper()}%")
        if n_clean:
            clauses.append("reg_number = ?")
            sql_params.append(n_clean)
        if active_only:
            clauses.append("(terminate_date IS NULL OR terminate_date = '')")

        where_sql = " AND ".join(clauses) if clauses else "1=1"
        matched = conn.execute(
            f"SELECT reg_number, principal_name, country, principal_reg_date, terminate_date, "
            f"       registrant_name, registrant_date, address, city, state, status "
            f"FROM fara_principals WHERE {where_sql} "
            f"ORDER BY principal_reg_date DESC, reg_number DESC LIMIT ?",
            (*sql_params, max(1, limit)),
        ).fetchall()
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM fara_principals WHERE {where_sql}",
            tuple(sql_params),
        ).fetchone()
        total_matches = int(total_row[0]) if total_row else len(matched)
    finally:
        conn.close()

    probe_params = {
        k: v
        for k, v in {
            "query": q_clean,
            "principal": p_clean,
            "registrant": r_clean,
            "country": c_clean,
            "reg_number": n_clean,
            "active_only": active_only,
        }.items()
        if v
    }

    results: list[Result] = []
    for row in matched:
        (reg_num, p_name, ctry, p_date, t_date, r_name, r_date, addr, city, st, status) = row
        suffix = hashlib.sha256(f"{p_name}|{r_name}".encode()).hexdigest()[:8]
        rid = f"{SOURCE}:{reg_num or 'row'}:{suffix}"
        rec_payload = {
            "reg_number": reg_num, "foreign_principal": p_name, "country": ctry,
            "principal_reg_date": p_date, "terminate_date": t_date,
            "registrant_name": r_name, "registrant_date": r_date,
            "address": addr, "city": city, "state": st, "status": status,
        }
        try:
            store.put_record(rid, SOURCE, label, rec_payload)
        except Exception:
            pass
        url = f"https://efile.fara.gov/ords/fara/f?p=1381:200:::::P200_REG_NUMBER:{reg_num}" if reg_num else FARA_PRINCIPALS_URL
        title = f"[FARA #{reg_num}] {p_name or '(principal)'} ({ctry or '?'}) -> {r_name or '(registrant)'}"
        snippet = " | ".join(p for p in (
            f"status: {status}",
            f"registered: {p_date}" if p_date else "",
            f"terminated: {t_date}" if t_date else "",
            f"{city}, {st}".strip(", ") if (city or st) else "",
        ) if p)
        results.append(Result(
            url=url,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[SOURCE],
            meta={**rec_payload, "record_id": rid, "total_matches": total_matches},
        ))

    return finalize_bulk_query(
        label,
        source=SOURCE,
        endpoint=FARA_PRINCIPALS_URL,
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
