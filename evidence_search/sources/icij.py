"""ICIJ Offshore Leaks bulk SQLite client (`evidence-search icij`, Issue #5).

Indexes ICIJ's official Offshore Leaks Database CSV/ZIP release
(`full-oldb.LATEST.zip`: entities, officers, intermediaries, and
relationships across the Panama Papers, Paradise Papers, Pandora Papers,
Bahamas Leaks, and Offshore Leaks) into a local SQLite database via
`core/bulk.py`.

Every matching entity/officer automatically resolves its 1-hop relationships
(shareholders, directors, intermediaries, registered jurisdictions).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import zipfile

from ..core.bulk import bulk_root, check_bulk_cache, finalize_bulk_query, open_index_db
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, RateLimited, Result
from ..core.store import Store

SOURCE = "icij"
ICIJ_BULK_URL = "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="ICIJ Offshore Leaks Database (Panama Papers, Paradise Papers, Pandora Papers, Offshore Leaks, Bahamas Leaks).",
    not_searched=(
        "Offshore entities in non-leaked corporate registries (BVI, Cayman, Delaware, Wyoming)",
        "Post-leak beneficial ownership changes not reflected in the leaked document tranche",
    ),
    caveats=(
        "Name matching across transliterated offshore filings can miss initials or nominee-director structures; inclusion in ICIJ Offshore Leaks does not itself establish illegality.",
    ),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS icij_nodes (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    jurisdiction TEXT,
    country_codes TEXT,
    source_dataset TEXT,
    incorporation_date TEXT,
    status TEXT
);
CREATE TABLE IF NOT EXISTS icij_relationships (
    start_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    end_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_icij_norm ON icij_nodes(name_norm);
CREATE INDEX IF NOT EXISTS idx_icij_rel_start ON icij_relationships(start_id);
CREATE INDEX IF NOT EXISTS idx_icij_rel_end ON icij_relationships(end_id);
"""


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _infer_node_type(filename: str, row: dict) -> str:
    low = (filename or "").lower()
    if "officer" in low:
        return "officer"
    if "intermediar" in low:
        return "intermediary"
    if "address" in low:
        return "address"
    return str(row.get("node_type") or row.get("type") or "entity").lower()


def parse_icij_payload(raw: str | bytes) -> tuple[list[dict], list[dict]]:
    """Parse ICIJ Offshore Leaks bulk ZIP, CSV, or JSON payload."""
    nodes: list[dict] = []
    rels: list[dict] = []

    if isinstance(raw, bytes) and raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError("ICIJ zip archive contained no CSV files")
            for cname in csv_names:
                text = zf.read(cname).decode("utf-8-sig", errors="replace")
                reader = csv.DictReader(io.StringIO(text))
                fields = set(reader.fieldnames or [])
                if "node_id" in fields and "name" in fields:
                    for row in reader:
                        nid = str(row.get("node_id") or "").strip()
                        nm = str(row.get("name") or "").strip()
                        if nid and nm:
                            nodes.append({
                                "node_id": nid,
                                "node_type": _infer_node_type(cname, row),
                                "name": nm,
                                "name_norm": _norm_name(nm),
                                "jurisdiction": str(row.get("jurisdiction_description") or row.get("jurisdiction") or ""),
                                "country_codes": str(row.get("country_codes") or row.get("countries") or ""),
                                "source_dataset": str(row.get("sourceID") or ""),
                                "incorporation_date": str(row.get("incorporation_date") or ""),
                                "status": str(row.get("status") or ""),
                            })
                elif {"node_id_start", "node_id_end"} <= fields or {"START_ID", "END_ID"} <= fields:
                    for row in reader:
                        s_id = str(row.get("node_id_start") or row.get("START_ID") or "").strip()
                        e_id = str(row.get("node_id_end") or row.get("END_ID") or "").strip()
                        r_tp = str(row.get("rel_type") or row.get("TYPE") or "related_to").strip()
                        if s_id and e_id:
                            rels.append({"start_id": s_id, "rel_type": r_tp, "end_id": e_id})
        if not nodes and not rels:
            raise ValueError("ICIJ zip contained no recognizable node or relationship headers")
        return nodes, rels

    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else (raw or "")
    text = text.strip()
    if not text:
        raise ValueError("empty ICIJ payload")

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid ICIJ JSON: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            raise ValueError("ICIJ JSON missing `nodes` list")
        raw_nodes = data["nodes"]
        if not raw_nodes and not isinstance(data.get("relationships"), list):
            raise ValueError("ICIJ JSON `nodes: []` missing `relationships` envelope")
        for row in raw_nodes:
            if not isinstance(row, dict) or "node_id" not in row or "name" not in row:
                raise ValueError("ICIJ JSON node row missing required `node_id`/`name` fields")
            nid = str(row.get("node_id") or "").strip()
            nm = str(row.get("name") or "").strip()
            if nid and nm:
                nodes.append({
                    "node_id": nid,
                    "node_type": str(row.get("node_type") or "entity").lower(),
                    "name": nm,
                    "name_norm": _norm_name(nm),
                    "jurisdiction": str(row.get("jurisdiction") or ""),
                    "country_codes": str(row.get("country_codes") or ""),
                    "source_dataset": str(row.get("source_dataset") or row.get("sourceID") or ""),
                    "incorporation_date": str(row.get("incorporation_date") or ""),
                    "status": str(row.get("status") or ""),
                })
        for row in (data.get("relationships") or []):
            if isinstance(row, dict) and row.get("start_id") and row.get("end_id"):
                rels.append({
                    "start_id": str(row["start_id"]),
                    "rel_type": str(row.get("rel_type") or "related_to"),
                    "end_id": str(row["end_id"]),
                })
        return nodes, rels

    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    if not ({"node_id", "name"} <= fields):
        raise ValueError(f"Unrecognized ICIJ CSV headers: {reader.fieldnames}")
    for row in reader:
        nid = str(row.get("node_id") or "").strip()
        nm = str(row.get("name") or "").strip()
        if nid and nm:
            nodes.append({
                "node_id": nid,
                "node_type": _infer_node_type("", row),
                "name": nm,
                "name_norm": _norm_name(nm),
                "jurisdiction": str(row.get("jurisdiction_description") or row.get("jurisdiction") or ""),
                "country_codes": str(row.get("country_codes") or row.get("countries") or ""),
                "source_dataset": str(row.get("sourceID") or ""),
                "incorporation_date": str(row.get("incorporation_date") or ""),
                "status": str(row.get("status") or ""),
            })
    return nodes, rels


def conn_for(cache_dir: Path | None = None) -> sqlite3.Connection:
    return open_index_db(cache_dir or bulk_root(SOURCE, "EVIDENCE_ICIJ_DIR"), SCHEMA)


def index_icij_data(conn: sqlite3.Connection, nodes: list[dict], rels: list[dict]) -> int:
    with conn:
        conn.execute("DELETE FROM icij_nodes")
        conn.execute("DELETE FROM icij_relationships")
        conn.executemany(
            "INSERT OR REPLACE INTO icij_nodes "
            "(node_id, node_type, name, name_norm, jurisdiction, country_codes, "
            " source_dataset, incorporation_date, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (n["node_id"], n["node_type"], n["name"], n["name_norm"],
                 n["jurisdiction"], n["country_codes"], n["source_dataset"],
                 n["incorporation_date"], n["status"])
                for n in nodes
            ],
        )
        conn.executemany(
            "INSERT INTO icij_relationships (start_id, rel_type, end_id) VALUES (?, ?, ?)",
            [(r["start_id"], r["rel_type"], r["end_id"]) for r in rels],
        )
    return len(nodes)


def search(query: str, *, node_type: str = "all", jurisdiction: str | None = None,
           dataset: str | None = None, refresh: bool = False, limit: int = 25,
           store: Store | None = None, limiter: Limiter | None = None,
           cache_dir: Path | None = None, use_cache: bool = True):
    """Search ICIJ Offshore Leaks entities, officers, and intermediaries."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (query or "").strip()
    q_norm = _norm_name(q_clean)
    if not q_norm:
        return AccessBlocker(
            query=q_clean,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "empty-query"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=ICIJ_BULK_URL,
            detail="ICIJ query must contain alphanumeric characters.",
        )

    searched = f"ICIJ Offshore Leaks ({q_clean})"
    cache_params = {
        "node_type": node_type,
        "jurisdiction": jurisdiction or "",
        "dataset": dataset or "",
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
    results: list[Result] = []
    try:
        count_row = conn.execute("SELECT COUNT(*) FROM icij_nodes").fetchone()
        if refresh or not (count_row and count_row[0] > 0):
            allowed, retry, why = limiter.reserve(SOURCE)
            if not allowed:
                return RateLimited(
                    query=q_clean,
                    coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                    source=SOURCE,
                    retry_after_s=int(retry) if retry else None,
                    detail=why,
                )
            body, err = fetch(ICIJ_BULK_URL, source=SOURCE, query=q_clean, binary=True, timeout=120.0)
            limiter.note_outcome(SOURCE, err)
            if err:
                return err
            try:
                nodes, rels = parse_icij_payload(body or b"")
                index_icij_data(conn, nodes, rels)
            except ValueError as e:
                return AccessBlocker(
                    query=q_clean,
                    coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse-failed"}),
                    mechanism=Blocker.SERVER_ERROR,
                    url=ICIJ_BULK_URL,
                    detail=f"{e} -- NOT an absence.",
                )

        clauses = ["name_norm LIKE ?"]
        params: list[str] = [f"%{q_norm}%"]
        if node_type and node_type != "all":
            clauses.append("node_type = ?")
            params.append(node_type.lower())
        if jurisdiction:
            clauses.append("UPPER(jurisdiction) LIKE ?")
            params.append(f"%{jurisdiction.strip().upper()}%")
        if dataset:
            clauses.append("UPPER(source_dataset) LIKE ?")
            params.append(f"%{dataset.strip().upper()}%")

        where_sql = " AND ".join(clauses)
        matched = conn.execute(
            f"SELECT node_id, node_type, name, jurisdiction, country_codes, source_dataset, incorporation_date, status "
            f"FROM icij_nodes WHERE {where_sql} LIMIT ?",
            (*params, max(1, limit)),
        ).fetchall()
        tot_row = conn.execute(f"SELECT COUNT(*) FROM icij_nodes WHERE {where_sql}", tuple(params)).fetchone()
        total_matches = int(tot_row[0]) if tot_row else len(matched)

        probe_params = {
            k: v
            for k, v in {
                "node_type": node_type,
                "jurisdiction": jurisdiction,
                "dataset": dataset,
            }.items()
            if v and v != "all"
        }

        for nid, ntype, name, jur, cc, src_ds, inc_dt, status in matched:
            conns_out = conn.execute(
                "SELECT r.rel_type, n.node_id, n.name, n.node_type "
                "FROM icij_relationships r JOIN icij_nodes n ON r.end_id = n.node_id "
                "WHERE r.start_id = ? LIMIT 10",
                (nid,),
            ).fetchall()
            conns_in = conn.execute(
                "SELECT r.rel_type, n.node_id, n.name, n.node_type "
                "FROM icij_relationships r JOIN icij_nodes n ON r.start_id = n.node_id "
                "WHERE r.end_id = ? LIMIT 10",
                (nid,),
            ).fetchall()
            connections = [
                f"{n_nm} [{rt} ->]" for rt, _, n_nm, _ in conns_out
            ] + [
                f"{n_nm} [<- {rt}]" for rt, _, n_nm, _ in conns_in
            ]
            rid = f"{SOURCE}:{nid}"
            rec_payload = {
                "node_id": nid, "node_type": ntype, "name": name,
                "jurisdiction": jur, "country_codes": cc,
                "source_dataset": src_ds, "incorporation_date": inc_dt,
                "status": status, "connections": connections,
            }
            try:
                store.put_record(rid, SOURCE, q_clean, rec_payload)
            except Exception:
                pass
            url = f"https://offshoreleaks.icij.org/nodes/{nid}"
            title = f"[{ntype.upper()} #{nid}] {name} ({jur or cc or '?'})"
            snippet = " | ".join(p for p in (
                f"dataset: {src_ds}" if src_ds else "",
                f"status: {status}" if status else "",
                f"links: {', '.join(connections[:3])}" if connections else "",
            ) if p)
            results.append(Result(
                url=url,
                title=title[:180],
                snippet=snippet[:360],
                source=SOURCE,
                engines=[SOURCE],
                index_origin=["own-index"],
                meta={**rec_payload, "record_id": rid, "total_matches": total_matches},
            ))
    finally:
        conn.close()

    return finalize_bulk_query(
        q_clean,
        source=SOURCE,
        endpoint=ICIJ_BULK_URL,
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
