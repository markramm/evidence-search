"""FEC campaign finance — committee-to-committee and committee-to-candidate
transactions, bulk first.

**Access shape, verified 2026-09-29 (#16):** the OpenFEC API's `DEMO_KEY`
returned `429` with `Retry-After: 34289` on its first call that day -- exhausted
by unrelated traffic before any use. The bulk files need no key at all:
`https://www.fec.gov/files/bulk-downloads/<cycle>/{pas2,oth}<yy>.zip`, 9-36 MB
per cycle. This source downloads them once per cycle into a local cache, builds
a small SQLite index over committee IDs, and answers from the index after that
-- which is also why it answers "did committee X give to committee Y" in
seconds instead of a live-search round trip per query.

**Two bulk files, and they OVERLAP for committee-to-candidate rows.** `pas2`
(`itpas2.txt`, 22 fields) is "any committee transaction to a candidate" --
in practice this captures the GIVING committee's own Schedule B report; the
RECEIVING candidate committee's matching Schedule A receipt usually does not
appear here at all (verified: Rogers for Congress and Mike Rogers for
Congress never separately report Purdue Pharma PAC's contributions in this
file -- only Purdue PAC's own filing does). `oth` (`itoth.txt`, 21 fields) is
documented as committee-to-committee transactions, but it is NOT disjoint
from `pas2`: every one of those same Purdue-PAC-to-Rogers contributions ALSO
appears in `oth`, byte-identical down to `SUB_ID` (FEC's own globally unique
schedule-line identifier). `_dedupe_by_sub_id` collapses that duplication so
a contribution is counted once; querying both files is still necessary,
because `oth` is also where GENUINE independent corroboration lives -- Purdue
Pharma PAC's $1,000 to its allies' leadership PAC in 2006 appears as a
Schedule B disbursement filed by C00370643 AND, under a DIFFERENT `SUB_ID`,
as a Schedule A receipt filed by C00370791. `between()` queries both files
and both directions and says, per transaction, whether only one side's
report is in hand.

**Refunds are netted, never dropped.** A negative `TRANSACTION_AMT` IS a
refund row (FEC's own convention -- no separate "refund" flag), and silently
filtering `amount > 0` would hide it. Verified case: Purdue Pharma PAC gave
Mike Rogers' Alabama committee (C00367862) $1,000 on 2007-10-09 and the SAME
pair shows a -$1,000 row on 2007-12-31 -- net $0, two line items, both shown.

**Committee-name lookup is a substring match over the committee master file
(`cm<yy>.zip`), and warns on more than one committee ID.** Two different
people named Mike Rogers filed separate House committees in the same era
(Michigan's "ROGERS FOR CONGRESS", C00343863, and Alabama's "MIKE ROGERS FOR
CONGRESS", C00367862) -- the exact same-surname trap usaspending's recipient
search already warns about.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path

import httpx

from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, Probe,
                            RateLimited, Result, verified_absence)
from ..core.store import Store

SOURCE = "fec"
BULK_BASE = "https://www.fec.gov/files/bulk-downloads"

#: itpas2.txt -- committee transactions TO a candidate's committee. The giving
#: committee's own report; the receiving candidate committee rarely re-reports
#: the same transaction here (see module docstring).
PAS2_FIELDS = ["CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM",
              "TRANSACTION_TP", "ENTITY_TP", "NAME", "CITY", "STATE", "ZIP_CODE",
              "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT",
              "OTHER_ID", "CAND_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD",
              "MEMO_TEXT", "SUB_ID"]

#: itoth.txt -- committee-to-committee transactions (no candidate on either
#: side). Both committees routinely file their own side of the same transfer.
OTH_FIELDS = ["CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM",
             "TRANSACTION_TP", "ENTITY_TP", "NAME", "CITY", "STATE", "ZIP_CODE",
             "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT",
             "OTHER_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD", "MEMO_TEXT", "SUB_ID"]

#: cm.txt -- the committee master file, one row per registered committee ID.
CM_FIELDS = ["CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY",
            "CMTE_ST", "CMTE_ZIP", "CMTE_DSGN", "CMTE_TP", "CMTE_PTY_AFFILIATION",
            "CMTE_FILING_FREQ", "ORG_TP", "CONNECTED_ORG_NM", "CAND_ID"]

FIELDS = {"pas2": PAS2_FIELDS, "oth": OTH_FIELDS, "cm": CM_FIELDS}
TXT_NAME = {"pas2": "itpas2.txt", "oth": "itoth.txt", "cm": "cm.txt"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fec_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL, cycle INTEGER NOT NULL,
    cmte_id TEXT NOT NULL, other_id TEXT, cand_id TEXT,
    name TEXT, city TEXT, state TEXT,
    transaction_tp TEXT, entity_tp TEXT, transaction_dt TEXT, transaction_amt REAL,
    image_num TEXT, tran_id TEXT, file_num TEXT, memo_cd TEXT, memo_text TEXT,
    sub_id TEXT, rpt_tp TEXT, transaction_pgi TEXT, amndt_ind TEXT
);
CREATE INDEX IF NOT EXISTS idx_fec_cmte ON fec_transactions(cmte_id, cycle);
CREATE INDEX IF NOT EXISTS idx_fec_other ON fec_transactions(other_id, cycle);
CREATE TABLE IF NOT EXISTS fec_committees (
    cmte_id TEXT, cycle INTEGER, cmte_nm TEXT, cmte_st TEXT, cmte_tp TEXT,
    cmte_pty TEXT, cand_id TEXT, PRIMARY KEY (cmte_id, cycle)
);
CREATE INDEX IF NOT EXISTS idx_fec_cmte_name ON fec_committees(cmte_nm);
CREATE TABLE IF NOT EXISTS fec_indexed_files (
    file TEXT, cycle INTEGER, indexed_at REAL, row_count INTEGER,
    PRIMARY KEY (file, cycle)
);
"""


def _cache_dir() -> Path:
    """Where downloaded bulk zips and the derived SQLite index live.

    A RUNTIME cache, never checked in -- override with EVIDENCE_FEC_CACHE. The
    default sits alongside the existing ~/.evidence-search/ store and archive
    rather than inside the repo, so there is nothing here for a developer to
    gitignore by hand; .gitignore still carries a belt-and-suspenders entry
    for anyone who points the override at a path under the repo.
    """
    override = os.environ.get("EVIDENCE_FEC_CACHE")
    return Path(override) if override else Path.home() / ".evidence-search" / "fec_bulk"


def conn_for(cache_dir: Path | None = None) -> sqlite3.Connection:
    d = cache_dir or _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(d / "index.db")
    # A single cycle's oth/pas2 file is hundreds of thousands of rows (oth08:
    # ~690K). The rollback journal's default fsync-per-commit made a cold
    # 3-cycle index take minutes of mostly syscall-bound wall time (3m26s
    # real against 53s of CPU, verified against the Purdue/Rogers cycles) --
    # WAL + NORMAL durability is the same tradeoff Store() already makes for
    # its own ledger, and here it is the difference between "bulk-first" and
    # "bulk-eventually".
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _bulk_url(base: str, cycle: int) -> str:
    return f"{BULK_BASE}/{cycle}/{base}{cycle % 100:02d}.zip"


#: A 2-year FEC cycle is named by its even year (2005-2006 is cycle "2006").
def _cycles_in_range(start_year: int, end_year: int) -> list[int]:
    lo = start_year + (start_year % 2)
    hi = end_year + (end_year % 2)
    return list(range(lo, hi + 1, 2))


_CYCLE_RANGE_RE = re.compile(r"^\s*(\d{4})\s*(?:-\s*(\d{4}))?\s*$")


def parse_cycle_range(s: str) -> tuple[int, int]:
    """'2006-2014' or '2008' -> (2006, 2014) or (2008, 2008). Raises ValueError."""
    m = _CYCLE_RANGE_RE.match(s or "")
    if not m:
        raise ValueError(f"{s!r} is not a cycle or cycle range, e.g. '2008' or '2006-2014'")
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if hi < lo:
        raise ValueError(f"{s!r}: end year is before the start year")
    return lo, hi


def _parse_row(base: str, line: str) -> dict | None:
    parts = line.rstrip("\r\n").split("|")
    fields = FIELDS[base]
    if len(parts) != len(fields):
        return None
    return dict(zip(fields, parts))


def _download(base: str, cycle: int, cache_dir: Path, timeout: float = 180.0):
    """Download+cache one bulk zip. Returns (path, None) or (None, Outcome)."""
    raw_dir = cache_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"{base}{cycle}.zip"
    if dest.exists() and dest.stat().st_size > 0:
        return dest, None
    url = _bulk_url(base, cycle)
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "evidence-search"}) as r:
            if r.status_code == 404:
                return None, AccessBlocker(
                    query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], errored={SOURCE: "not-found"}),
                    mechanism=Blocker.NOT_FOUND, url=url,
                    detail=f"no {base} bulk file published for cycle {cycle}")
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                return None, RateLimited(
                    query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                    source=SOURCE, retry_after_s=int(ra) if ra and ra.isdigit() else 60, detail="HTTP 429")
            if r.status_code >= 400:
                return None, AccessBlocker(
                    query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], errored={SOURCE: "status"}),
                    mechanism=Blocker.SERVER_ERROR, url=url, detail=f"HTTP {r.status_code}")
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
            tmp.rename(dest)
    except httpx.TimeoutException as e:
        return None, RateLimited(
            query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
            source=SOURCE, retry_after_s=60, detail=f"timeout: {e}")
    except httpx.HTTPError as e:
        return None, AccessBlocker(
            query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], errored={SOURCE: "http"}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))
    return dest, None


def _index_rows(conn: sqlite3.Connection, base: str, cycle: int, rows: list[dict]) -> int:
    """Insert already-parsed rows into the local index. Pure, no I/O -- the
    regression tests call this directly with fixture rows and never touch the
    network, per CONTRIBUTING's absence discipline AND the house rule that the
    test suite must not download anything.

    `executemany` rather than one `execute()` per row: a single oth cycle runs
    to ~690K rows, and per-statement overhead (not disk I/O -- see
    `conn_for`'s WAL note) was the other half of what made a cold multi-cycle
    index take minutes.
    """
    if base == "cm":
        batch = [(d.get("CMTE_ID"), cycle, d.get("CMTE_NM"), d.get("CMTE_ST"),
                 d.get("CMTE_TP"), d.get("CMTE_PTY_AFFILIATION"), d.get("CAND_ID"))
                for d in rows]
        conn.executemany(
            "INSERT OR REPLACE INTO fec_committees VALUES (?,?,?,?,?,?,?)", batch)
        conn.commit()
        return len(batch)

    def _amt(d: dict) -> float | None:
        v = d.get("TRANSACTION_AMT")
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    batch = [(base, cycle, d.get("CMTE_ID"), d.get("OTHER_ID"), d.get("CAND_ID"),
             d.get("NAME"), d.get("CITY"), d.get("STATE"), d.get("TRANSACTION_TP"),
             d.get("ENTITY_TP"), d.get("TRANSACTION_DT"), _amt(d), d.get("IMAGE_NUM"),
             d.get("TRAN_ID"), d.get("FILE_NUM"), d.get("MEMO_CD"), d.get("MEMO_TEXT"),
             d.get("SUB_ID"), d.get("RPT_TP"), d.get("TRANSACTION_PGI"), d.get("AMNDT_IND"))
            for d in rows]
    conn.executemany(
        "INSERT INTO fec_transactions (file, cycle, cmte_id, other_id, cand_id, "
        "name, city, state, transaction_tp, entity_tp, transaction_dt, "
        "transaction_amt, image_num, tran_id, file_num, memo_cd, memo_text, "
        "sub_id, rpt_tp, transaction_pgi, amndt_ind) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    conn.commit()
    return len(batch)


def _mark_indexed(conn: sqlite3.Connection, base: str, cycle: int, n: int) -> None:
    conn.execute("INSERT OR REPLACE INTO fec_indexed_files VALUES (?,?,?,?)",
                (base, cycle, time.time(), n))
    conn.commit()


def is_indexed(conn: sqlite3.Connection, base: str, cycle: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM fec_indexed_files WHERE file=? AND cycle=?", (base, cycle)
    ).fetchone() is not None


def ensure_indexed(base: str, cycle: int, conn: sqlite3.Connection, cache_dir: Path,
                   limiter: Limiter):
    """Make sure (base, cycle) is in the local index, downloading it if not.

    Returns (ok, outcome_or_None). `ok=False` means the index does NOT cover
    this (base, cycle); `outcome` names why (blocked, rate-limited, or a
    parse/shape failure) so a caller can build dirty Coverage rather than
    certifying an absence over data that was never actually read.
    """
    if is_indexed(conn, base, cycle):
        return True, None

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return False, RateLimited(
            query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
            source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    zpath, err = _download(base, cycle, cache_dir)
    limiter.note_outcome(SOURCE, err)
    if err is not None:
        return False, err

    fname = TXT_NAME[base]
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            if fname not in names:
                return False, AccessBlocker(
                    query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                    mechanism=Blocker.SERVER_ERROR, url=_bulk_url(base, cycle),
                    detail=(f"{fname!r} not found inside the archive (has {names}) -- "
                            "the bulk file's shape may have changed"))
            raw = zf.read(fname).decode("latin-1")
    except zipfile.BadZipFile as e:
        return False, AccessBlocker(
            query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
            mechanism=Blocker.SERVER_ERROR, url=_bulk_url(base, cycle),
            detail=f"downloaded file is not a valid zip ({e})")

    lines = [ln for ln in raw.split("\n") if ln.strip()]
    rows, bad = [], 0
    for ln in lines:
        d = _parse_row(base, ln)
        if d is None:
            bad += 1
        else:
            rows.append(d)
    # A parse failure across most lines is a format change, not an empty
    # cycle -- the FEC does not file zero lines in a real reporting cycle, and
    # certifying that as an absence would be exactly the dollar_sum/#17 defect.
    if lines and bad / len(lines) > 0.5:
        return False, AccessBlocker(
            query=f"{base}{cycle}", coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
            mechanism=Blocker.SERVER_ERROR, url=_bulk_url(base, cycle),
            detail=(f"{bad}/{len(lines)} lines did not match the expected "
                    f"{len(FIELDS[base])}-field shape -- the bulk file format may "
                    "have changed"))

    n = _index_rows(conn, base, cycle, rows)
    _mark_indexed(conn, base, cycle, n)
    return True, None


# --- querying ------------------------------------------------------------------

def _rows_between(conn: sqlite3.Connection, a: str, b: str, cycles: list[int]) -> list[dict]:
    if not cycles:
        return []
    placeholders = ",".join("?" * len(cycles))
    q = (f"SELECT file, cycle, cmte_id, other_id, cand_id, name, transaction_dt, "
        f"transaction_amt, image_num, tran_id, sub_id, rpt_tp, transaction_tp, "
        f"memo_text FROM fec_transactions WHERE cycle IN ({placeholders}) AND "
        f"((cmte_id=? AND other_id=?) OR (cmte_id=? AND other_id=?)) "
        f"ORDER BY transaction_dt, file")
    args = list(cycles) + [a, b, b, a]
    cur = conn.execute(q, args)
    cols = [c[0] for c in cur.description]
    return _dedupe_by_sub_id([dict(zip(cols, r)) for r in cur.fetchall()])


def _dedupe_by_sub_id(rows: list[dict]) -> list[dict]:
    """The SAME FEC schedule line can appear in BOTH pas2 and oth.

    Verified live (#16): Purdue Pharma PAC's $1,000 to Rogers for Congress
    (MI) is in 2008's itpas2.txt AND itoth.txt, with the IDENTICAL SUB_ID,
    IMAGE_NUM and TRAN_ID in both -- the two bulk extracts are not disjoint
    for a committee-to-candidate transaction, and summing both copies put
    five $1,000 contributions at $10,000. SUB_ID is FEC's own globally unique
    schedule-line identifier, so it is the dedup key (falling back to
    file:tran_id:image_num on the rare row missing one). The pas2 copy is
    kept over the oth copy when both exist, because only pas2 carries CAND_ID.
    """
    best: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        key = r.get("sub_id") or f"{r['file']}:{r['tran_id']}:{r['image_num']}"
        if key not in best:
            order.append(key)
            best[key] = r
        elif r["file"] == "pas2" and best[key]["file"] != "pas2":
            best[key] = r
    return [best[k] for k in order]


def _row_result(r: dict, totals: dict, counted_in_total: bool) -> Result:
    amt = r["transaction_amt"]
    is_refund = amt is not None and amt < 0
    amt_txt = f"${amt:,.2f}" if amt is not None else "(unreadable amount)"
    return Result(
        url=(f"https://www.fec.gov/data/receipts/?committee_id={r['cmte_id']}"
            f"&two_year_transaction_period={r['cycle']}"),
        title=(f"{r['cmte_id']} → {r['other_id']}: {'REFUND ' if is_refund else ''}"
              f"{amt_txt} on {r['transaction_dt']}"),
        snippet=(f"{r['file']} {r['cycle']} | image {r['image_num']} | "
                f"tran {r['tran_id']} | rpt {r['rpt_tp']} | type {r['transaction_tp']}"),
        source=SOURCE, engines=[SOURCE], index_origin=["own-index"],
        meta={"record_id": f"{SOURCE}:{r['file']}:{r['sub_id'] or r['tran_id'] or r['image_num']}",
              "file": r["file"], "cycle": r["cycle"], "filer_committee": r["cmte_id"],
              # False on a receipt-side row when the giving side ALSO filed --
              # shown for corroboration, excluded from gross/net so a
              # corroborated transaction is never counted twice.
              "counted_in_total": counted_in_total,
              "counterparty_committee": r["other_id"], "amount": amt, "date": r["transaction_dt"],
              "image_num": r["image_num"], "transaction_id": r["tran_id"],
              "report_type": r["rpt_tp"], "transaction_type": r["transaction_tp"],
              "is_refund": is_refund, **totals},
    )


def between(from_committee: str, to_committee: str, cycle_from: int, cycle_to: int,
           store: Store | None = None, limiter: Limiter | None = None,
           cache_dir: Path | None = None, use_cache: bool = True):
    """committee X -> committee Y across cycles C1-C2, both sides where both
    reported, refunds netted and shown.

    `use_cache` names the query-result cache (replay a prior answer without
    re-touching the index); it does NOT affect the bulk-file/index cache,
    which is content-addressed by (file, cycle) and always reused once built.
    """
    cache_dir = cache_dir or _cache_dir()
    store = store or Store()
    limiter = limiter or Limiter(store)
    conn = conn_for(cache_dir)

    cycles = _cycles_in_range(cycle_from, cycle_to)
    query = f"{from_committee} -> {to_committee}, cycles {cycle_from}-{cycle_to}"

    key = None
    if use_cache:
        from ..core.store import cache_key
        key = cache_key(SOURCE, query, cycles=",".join(map(str, cycles)))
        entry = store.get_entry(key, with_meta=True)
        if entry is not None:
            from ..core.results import replay_cached
            return replay_cached(query, entry[0], entry[1], source=SOURCE,
                                 searched=f"FEC transactions ({query})", meta=entry[2])

    indexed: list[tuple[str, int]] = []
    failed: dict[tuple[str, int], object] = {}
    for base in ("pas2", "oth"):
        for cyc in cycles:
            ok, err = ensure_indexed(base, cyc, conn, cache_dir, limiter)
            if ok:
                indexed.append((base, cyc))
            else:
                failed[(base, cyc)] = err

    if not indexed:
        # Nothing could be read at all -- surface the first concrete reason
        # rather than a generic failure.
        return next(iter(failed.values()))

    rows = _rows_between(conn, from_committee, to_committee, cycles)

    errored = {f"{b}{c}": type(e).__name__ for (b, c), e in failed.items()}
    rate_limited = [f"{b}{c}" for (b, c), e in failed.items() if isinstance(e, RateLimited)]
    cov = Coverage(queried=[f"{b}{c}" for b, c in indexed] + list(errored),
                   responsive=[f"{b}{c}" for b, c in indexed],
                   rate_limited=rate_limited, errored=errored,
                   indexes=["own-index"])

    # When BOTH sides report (the oth.txt case), the same transfer appears
    # TWICE -- once as the giver's own Schedule B disbursement (TRANSACTION_TP
    # starting '2'), once as the receiver's own Schedule A receipt (starting
    # '1'). Summing every row double-counts every corroborated transaction:
    # verified live on the Purdue Pharma PAC -> leadership PAC pair, where
    # naive summation put 2006+2012 at $4,000 when $2,000 actually moved.
    # Disbursement rows are the canonical total; receipt-only rows are used
    # ONLY when the giving side never filed one (the single-side case this
    # source exists to flag), so a transaction that is corroborated is never
    # worth MORE than one that is not.
    disbursements = [r for r in rows if (r["transaction_tp"] or "")[:1] == "2"]
    basis = disbursements or rows
    basis_ids = {id(r) for r in basis}
    gross = sum(r["transaction_amt"] for r in basis if r["transaction_amt"] and r["transaction_amt"] > 0)
    refunded = -sum(r["transaction_amt"] for r in basis if r["transaction_amt"] and r["transaction_amt"] < 0)
    a_filed = any(r["cmte_id"] == from_committee for r in rows)
    b_filed = any(r["cmte_id"] == to_committee for r in rows)
    totals = {"gross_contributed": round(gross, 2), "refunded": round(refunded, 2),
             "net": round(gross - refunded, 2),
             "both_sides_reported": a_filed and b_filed,
             "cycles_indexed": sorted({c for _, c in indexed}),
             "cycles_not_indexed": sorted({c for _, c in failed}) if failed else []}

    not_searched = [
        "unitemized receipts under the itemization threshold",
        "transactions reported by only the side that files, where the other "
        "side's own report was not found in this pull (check "
        "meta.both_sides_reported on each row)",
        "reports filed for the current cycle but not yet processed into the "
        "bulk file FEC publishes",
        "individual (non-committee) contributions -- the itemized 'indiv' file",
    ]
    if failed:
        not_searched.append(
            "cycles that could not be indexed: " +
            ", ".join(f"{b} {c} ({type(e).__name__})" for (b, c), e in failed.items()))

    probes = [Probe(source=SOURCE, endpoint=f"{BULK_BASE}/<cycle>/{{pas2,oth}}<yy>.zip",
                    query=query, params={"from": from_committee, "to": to_committee,
                                         "cycles": ",".join(map(str, cycles))},
                    corpus="FEC bulk pas2 (committee->candidate) + oth "
                           "(committee->committee), both directions",
                    result_count=len(rows), exact_match_supported=True, at=time.time())]

    if not rows:
        if use_cache and key:
            try:
                from dataclasses import asdict
                store.put(key, SOURCE, [], ttl_s=86400,
                         meta={"probes": [asdict(p) for p in probes],
                               "not_searched": not_searched, "caveats": []})
            except Exception:
                pass
        return verified_absence(
            query, cov,
            f"no transactions between {from_committee} and {to_committee} in "
            f"FEC bulk data, cycles {cycle_from}-{cycle_to}",
            probes=probes, not_searched=not_searched, caveats=[])

    results = [_row_result(r, totals, id(r) in basis_ids) for r in rows]
    if use_cache and key:
        try:
            store.put(key, SOURCE, [r.__dict__ for r in results], ttl_s=86400)
        except Exception as e:
            cov.cache_write_refused = str(e)
    return Hit(query=query, coverage=cov, results=results)


# --- committee-name lookup -------------------------------------------------

def lookup_committee(name: str, cycle_from: int, cycle_to: int,
                     store: Store | None = None, limiter: Limiter | None = None,
                     cache_dir: Path | None = None, use_cache: bool = True):
    """Resolve a committee name to ID(s). Warns rather than guessing when more
    than one distinct committee ID matches -- same-surname candidates in
    different states/offices are common (two Mike Rogers committees, #16)."""
    cache_dir = cache_dir or _cache_dir()
    store = store or Store()
    limiter = limiter or Limiter(store)
    conn = conn_for(cache_dir)
    cycles = _cycles_in_range(cycle_from, cycle_to)
    query = f"committee name {name!r}, cycles {cycle_from}-{cycle_to}"

    indexed, failed = [], {}
    for cyc in cycles:
        ok, err = ensure_indexed("cm", cyc, conn, cache_dir, limiter)
        if ok:
            indexed.append(cyc)
        else:
            failed[cyc] = err
    if not indexed:
        return next(iter(failed.values()))

    rows = conn.execute(
        "SELECT DISTINCT cmte_id, cmte_nm, cmte_st, cmte_tp, cmte_pty, cand_id "
        "FROM fec_committees WHERE cycle IN ({}) AND UPPER(cmte_nm) LIKE ?".format(
            ",".join("?" * len(indexed))),
        [*indexed, f"%{name.upper()}%"]).fetchall()

    cov = Coverage(queried=[f"cm{c}" for c in indexed], responsive=[f"cm{c}" for c in indexed],
                   errored={f"cm{c}": type(e).__name__ for c, e in failed.items()},
                   indexes=["own-index"])
    not_searched = ["committee names changed or merged outside the cycles indexed",
                    "candidates/committees never registered with the FEC"]
    probes = [Probe(source=SOURCE, endpoint=f"{BULK_BASE}/<cycle>/cm<yy>.zip",
                    query=query, params={"name": name, "cycles": ",".join(map(str, cycles))},
                    corpus="FEC committee master file (cm.txt), substring match on name",
                    result_count=len(rows), exact_match_supported=False, at=time.time())]

    if not rows:
        return verified_absence(
            query, cov, f"no committee name matching {name!r} in cycles {cycle_from}-{cycle_to}",
            probes=probes, not_searched=not_searched,
            caveats=["name search is a case-insensitive SUBSTRING match"])

    distinct_ids = sorted({r[0] for r in rows})
    warning = (
        f"{len(distinct_ids)} DISTINCT COMMITTEE IDs matched {name!r}: "
        f"{', '.join(distinct_ids)}. Confirm which one before using it -- "
        "same-surname candidates in different states/offices file separate "
        "committees (verified case: 'Rogers for Congress' C00343863, Michigan, "
        "vs 'Mike Rogers for Congress' C00367862, Alabama)."
        if len(distinct_ids) > 1 else None)

    results = []
    for cmte_id, nm, st, tp, pty, cand_id in rows:
        results.append(Result(
            url=f"https://www.fec.gov/data/committee/{cmte_id}/",
            title=f"{nm} ({cmte_id})", snippet=f"{st or '?'} | {tp or '?'} | {pty or '?'}",
            source=SOURCE, engines=[SOURCE], index_origin=["own-index"],
            meta={"committee_id": cmte_id, "committee_name": nm, "state": st,
                  "committee_type": tp, "party": pty, "candidate_id": cand_id,
                  "multiple_committees_warning": warning},
        ))
    return Hit(query=query, coverage=cov, results=results)
