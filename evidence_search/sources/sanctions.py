"""Sanctions — OFAC SDN and EU Consolidated Financial Sanctions lists (#3).

Verified live 2026-08-19 and 2026-10-30:
  * OFAC SDN (open bulk CSV, no key, ~5.6 MB):
    `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV`
  * EU Consolidated Financial Sanctions (open bulk CSV, no key, ~25 MB, `;`-delimited):
    `https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw`

The research question this answers: *is this entity or individual under U.S. or
EU sanction, under which programme, and since when?*

Architecture & Transliteration Discipline (incorporating @itulo's field notes on #3):
  1. Built on the shared local SQLite bulk-index architecture (`~/.evidence-search/sanctions_bulk/index.db`,
     overridable via `EVIDENCE_SANCTIONS_CACHE`).
  2. In the EU CSV, one subject spans many rows (each alias, birth date, address,
     and regulation is its own row sharing `Entity_LogicalId`). We group by
     `Entity_LogicalId` and index every `NameAlias_WholeName` as a searchable
     alias of that entity. In OFAC SDN, we index the primary name plus any `-0-`
     aka/fka remarks extracted from the remarks column.
  3. Two-tier matching:
     * Normalize strings with NFKD, diacritic stripping, casefolding, punctuation
       removal, and legal-suffix removal (`LLC`, `INC`, `LTD`, `GMBH`, `PJSC`,
       `JSC`, `OOO`, `AO`, `SA`, `BV`).
     * `exact_match`: normalized query equals a normalized primary name or
       registered alias (or token-set exact match).
     * `possible_match`: token-sort Jaro-Winkler similarity >= threshold (default
       0.85). Fuzzy hits are ALWAYS labelled `possible_match`, never `exact_match`.
  4. Absence semantics: a `VerifiedAbsence` requires every requested list (`ofac`
     and `eu` by default) to be indexed and healthy, and states the index age
     and transliteration bounds in `caveats`.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from ..core.bulk import bulk_root, download_bulk_file, open_index_db
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, Probe,
                            RateLimited, Result, absence_meta, replay_cached,
                            verified_absence)
from ..core.store import Store, cache_key

SOURCE = "sanctions"

URLS = {
    "ofac": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
    "eu": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sanctions_entities (
    list_name TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    primary_name TEXT NOT NULL,
    entity_type TEXT,
    programme TEXT,
    birth_year TEXT,
    country TEXT,
    pub_url TEXT,
    remarks TEXT,
    PRIMARY KEY (list_name, entity_id)
);
CREATE TABLE IF NOT EXISTS sanctions_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_name TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    alias_name TEXT NOT NULL,
    norm_name TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sanctions_norm ON sanctions_aliases(norm_name);
CREATE INDEX IF NOT EXISTS idx_sanctions_ent ON sanctions_aliases(list_name, entity_id);
CREATE TABLE IF NOT EXISTS sanctions_indexed_files (
    list_name TEXT PRIMARY KEY,
    indexed_at REAL NOT NULL,
    entity_count INTEGER NOT NULL,
    alias_count INTEGER NOT NULL
);
"""

_LEGAL_SUFFIXES = {
    "llc", "inc", "incorporated", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "ag", "sa", "sarl", "bv", "nv", "plc",
    "pjsc", "ojsc", "jsc", "ooo", "ao", "zao", "pao", "srl", "spa",
}


def normalize_name(name: str) -> str:
    """NFKD, strip diacritics, lowercase, remove punctuation and legal suffixes."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Replace punctuation (including commas in "LAST, First") with spaces
    tokens = re.findall(r"[a-z0-9]+", ascii_str.lower())
    filtered = [t for t in tokens if t not in _LEGAL_SUFFIXES] or tokens
    return " ".join(filtered)


def token_sort_key(norm: str) -> str:
    return " ".join(sorted(norm.split()))


def _jaro_winkler(s1: str, s2: str) -> float:
    """Pure-Python Jaro-Winkler similarity in [0.0, 1.0]."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if not len1 or not len2:
        return 0.0
    match_dist = max(0, (max(len1, len2) // 2) - 1)
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    for i in range(len1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if not s2_matches[j] and s1[i] == s2[j]:
                s1_matches[i] = s2_matches[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t = 0
    k = 0
    for i in range(len1):
        if s1_matches[i]:
            while not s2_matches[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    jaro = ((matches / len1) + (matches / len2) + ((matches - t / 2) / matches)) / 3.0
    prefix = 0
    for c1, c2 in zip(s1[:4], s2[:4]):
        if c1 == c2:
            prefix += 1
        else:
            break
    return jaro + prefix * 0.1 * (1.0 - jaro)


def similarity_score(query_norm: str, target_norm: str) -> float:
    """Token-sort Jaro-Winkler combined with token-set overlap."""
    if not query_norm or not target_norm:
        return 0.0
    if query_norm == target_norm:
        return 1.0
    q_sorted = token_sort_key(query_norm)
    t_sorted = token_sort_key(target_norm)
    if q_sorted == t_sorted:
        return 1.0
    jw = max(_jaro_winkler(query_norm, target_norm),
             _jaro_winkler(q_sorted, t_sorted))
    q_toks = set(query_norm.split())
    t_toks = set(target_norm.split())
    if q_toks and q_toks <= t_toks:
        # Query tokens are a strict subset (e.g. missing middle name / patronymic)
        jw = max(jw, 0.88 + 0.10 * (len(q_toks) / len(t_toks)))
    return round(min(0.999, jw), 4)


def _cache_dir() -> Path:
    return bulk_root(SOURCE, "EVIDENCE_SANCTIONS_CACHE")


def conn_for(cache_dir: Path | None = None) -> sqlite3.Connection:
    return open_index_db(cache_dir or _cache_dir(), SCHEMA)


_AKA_RE = re.compile(r"a\.k\.a\.\s*'([^']+)'|f\.k\.a\.\s*'([^']+)'", re.I)
_DOB_YEAR_RE = re.compile(r"\bDOB\s+[^;]*?\b((?:19|20)\d{2})\b", re.I)


def parse_ofac_csv(raw_text: str) -> tuple[list[dict], list[dict]]:
    """Parse OFAC SDN.CSV into `(entities, aliases)`. Raises ValueError on bad shape."""
    entities: list[dict] = []
    aliases: list[dict] = []
    reader = csv.reader(io.StringIO(raw_text))
    total_rows = 0
    valid_rows = 0
    for row in reader:
        if not row or len(row) == 1 and row[0].strip() in ("", "\x1a"):
            continue
        total_rows += 1
        if len(row) < 4 or not str(row[0]).strip().isdigit():
            continue
        valid_rows += 1
        ent_num = row[0].strip()
        name = row[1].strip().strip('"')
        sdn_type = row[2].strip()
        program = row[3].strip()
        remarks = row[11].strip() if len(row) > 11 else ""
        if remarks == "-0-":
            remarks = ""
        dob_m = _DOB_YEAR_RE.search(remarks)
        birth_year = dob_m.group(1) if dob_m else ""
        entities.append({
            "list_name": "ofac",
            "entity_id": ent_num,
            "primary_name": name,
            "entity_type": sdn_type if sdn_type != "-0-" else "entity",
            "programme": program if program != "-0-" else "",
            "birth_year": birth_year,
            "country": "",
            "pub_url": "https://sanctionssearch.ofac.treas.gov/",
            "remarks": remarks,
        })
        norm = normalize_name(name)
        if norm:
            aliases.append({
                "list_name": "ofac", "entity_id": ent_num,
                "alias_name": name, "norm_name": norm, "is_primary": 1,
            })
        for m in _AKA_RE.finditer(remarks):
            aka = (m.group(1) or m.group(2) or "").strip()
            aka_norm = normalize_name(aka)
            if aka_norm and aka_norm != norm:
                aliases.append({
                    "list_name": "ofac", "entity_id": ent_num,
                    "alias_name": aka, "norm_name": aka_norm, "is_primary": 0,
                })
    if not valid_rows or (total_rows and valid_rows / total_rows < 0.5):
        raise ValueError(f"OFAC SDN CSV shape changed ({valid_rows}/{total_rows} valid rows)")
    return entities, aliases


def parse_eu_csv(raw_text: str) -> tuple[list[dict], list[dict]]:
    """Parse EU Consolidated semicolon-delimited CSV into `(entities, aliases)`.

    Groups rows by `Entity_LogicalId` so every `NameAlias_WholeName` row is
    indexed as a searchable alias of that logical entity (#3).
    """
    sio = io.StringIO(raw_text.lstrip("\ufeff"))
    reader = csv.DictReader(sio, delimiter=";")
    headers = set(reader.fieldnames or [])
    if not {"Entity_LogicalId", "NameAlias_WholeName"} <= headers:
        raise ValueError(
            "EU sanctions CSV missing required columns `Entity_LogicalId` / "
            f"`NameAlias_WholeName` (got {sorted(headers)[:8]})")

    by_id: dict[str, dict] = {}
    aliases_by_id: dict[str, dict[str, tuple[str, int]]] = {}
    for row in reader:
        eid = (row.get("Entity_LogicalId") or "").strip()
        if not eid:
            continue
        whole = (row.get("NameAlias_WholeName") or "").strip()
        prog = (row.get("Entity_Regulation_Programme") or "").strip()
        pub_url = (row.get("Entity_Regulation_PublicationUrl") or "").strip()
        stype = (row.get("Entity_SubjectType_ClassificationCode") or row.get("Entity_SubjectType") or "").strip()
        byear = (row.get("BirthDate_Year") or "").strip()
        country = (row.get("Address_CountryIso2Code") or row.get("Citizenship_CountryIso2Code") or "").strip()
        remark = (row.get("Entity_Remark") or "").strip()

        ent = by_id.get(eid)
        if ent is None:
            ent = {
                "list_name": "eu",
                "entity_id": eid,
                "primary_name": whole or eid,
                "entity_type": stype,
                "programme": prog,
                "birth_year": byear,
                "country": country,
                "pub_url": pub_url,
                "remarks": remark,
            }
            by_id[eid] = ent
            aliases_by_id[eid] = {}
        else:
            if not ent["primary_name"] and whole:
                ent["primary_name"] = whole
            if not ent["programme"] and prog:
                ent["programme"] = prog
            if not ent["birth_year"] and byear:
                ent["birth_year"] = byear
            if not ent["country"] and country:
                ent["country"] = country
            if not ent["pub_url"] and pub_url:
                ent["pub_url"] = pub_url

        if whole:
            norm = normalize_name(whole)
            if norm and norm not in aliases_by_id[eid]:
                is_prim = 1 if whole == ent["primary_name"] else 0
                aliases_by_id[eid][norm] = (whole, is_prim)

    if not by_id:
        raise ValueError("EU sanctions CSV contained zero logical entities")

    entities = list(by_id.values())
    aliases = [
        {"list_name": "eu", "entity_id": eid, "alias_name": raw_alias,
         "norm_name": norm, "is_primary": is_prim}
        for eid, amap in aliases_by_id.items()
        for norm, (raw_alias, is_prim) in amap.items()
    ]
    return entities, aliases


def index_sanctions_data(conn: sqlite3.Connection, list_name: str,
                         entities: list[dict], aliases: list[dict]) -> None:
    """Replace `list_name` rows in the local SQLite index."""
    conn.execute("DELETE FROM sanctions_entities WHERE list_name=?", (list_name,))
    conn.execute("DELETE FROM sanctions_aliases WHERE list_name=?", (list_name,))
    conn.executemany(
        "INSERT OR REPLACE INTO sanctions_entities VALUES (?,?,?,?,?,?,?,?,?)",
        [(e["list_name"], e["entity_id"], e["primary_name"], e.get("entity_type"),
          e.get("programme"), e.get("birth_year"), e.get("country"),
          e.get("pub_url"), e.get("remarks")) for e in entities],
    )
    conn.executemany(
        "INSERT INTO sanctions_aliases (list_name, entity_id, alias_name, norm_name, is_primary) "
        "VALUES (?,?,?,?,?)",
        [(a["list_name"], a["entity_id"], a["alias_name"], a["norm_name"], a.get("is_primary", 0))
         for a in aliases],
    )
    conn.execute(
        "INSERT OR REPLACE INTO sanctions_indexed_files VALUES (?,?,?,?)",
        (list_name, time.time(), len(entities), len(aliases)),
    )
    conn.commit()


def ensure_list_indexed(list_name: str, conn: sqlite3.Connection, cache_dir: Path,
                        limiter: Limiter, force_refresh: bool = False):
    """Ensure `list_name` ('ofac' or 'eu') is indexed in SQLite."""
    row = conn.execute(
        "SELECT indexed_at, entity_count FROM sanctions_indexed_files WHERE list_name=?",
        (list_name,),
    ).fetchone()
    if row and row[1] > 0 and not force_refresh:
        return True, row[0], None

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return False, 0.0, RateLimited(
            query=list_name, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
            source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    url = URLS[list_name]
    dest = cache_dir / "raw" / f"{list_name}.csv"
    path, err = download_bulk_file(url, dest, source=SOURCE, query=list_name, force=force_refresh)
    limiter.note_outcome(SOURCE, err)
    if err is not None:
        return False, 0.0, err

    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        if list_name == "ofac":
            entities, aliases = parse_ofac_csv(raw_text)
        else:
            entities, aliases = parse_eu_csv(raw_text)
    except Exception as e:
        return False, 0.0, AccessBlocker(
            query=list_name, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
            mechanism=Blocker.SERVER_ERROR, url=url,
            detail=f"payload shape changed on {list_name} sanctions CSV ({e}) -- NOT an absence.")

    index_sanctions_data(conn, list_name, entities, aliases)
    now = time.time()
    return True, now, None


def search(query: str, *, lists: tuple[str, ...] = ("ofac", "eu"),
           exact_only: bool = False, threshold: float = 0.85,
           refresh: bool = False,
           store: Store | None = None, limiter: Limiter | None = None,
           cache_dir: Path | None = None, use_cache: bool = True):
    """Screen a name against OFAC SDN and EU Consolidated sanctions lists (#3)."""
    cache_dir = cache_dir or _cache_dir()
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_norm = normalize_name(query)
    if not q_norm:
        return AccessBlocker(
            query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "empty-query"}),
            mechanism=Blocker.WRONG_ID_TYPE, url="",
            detail="Query contains no alphanumeric characters after normalization. NOT an absence.")

    key = cache_key(SOURCE, q_norm, lists=",".join(lists), exact=exact_only, threshold=threshold)
    if use_cache and not refresh:
        entry = store.get_entry(key, with_meta=True)
        if entry is not None:
            replayed = replay_cached(query, entry[0], entry[1], source=SOURCE,
                                     searched=f"sanctions ({','.join(lists)})",
                                     index_origin="own-index", meta=entry[2])
            if replayed is not None:
                return replayed

    conn = conn_for(cache_dir)
    try:
        indexed: dict[str, float] = {}
        failed: dict[str, object] = {}
        for lst in lists:
            ok, ts, err = ensure_list_indexed(lst, conn, cache_dir, limiter, force_refresh=refresh)
            if ok:
                indexed[lst] = ts
            else:
                failed[lst] = err

        if not indexed:
            return next(iter(failed.values()))

        errored = {f"{SOURCE}:{k}": type(e).__name__ for k, e in failed.items()
                   if not isinstance(e, RateLimited)}
        rate_limited = [f"{SOURCE}:{k}" for k, e in failed.items() if isinstance(e, RateLimited)]
        cov = Coverage(
            queried=[f"{SOURCE}:{k}" for k in lists],
            responsive=[f"{SOURCE}:{k}" for k in indexed],
            rate_limited=rate_limited,
            errored=errored,
            indexes=["own-index"],
        )

        placeholders = ",".join("?" * len(indexed))
        q_sorted = token_sort_key(q_norm)

        # Fetch candidate aliases from the indexed lists
        alias_sql = (
            f"SELECT list_name, entity_id, alias_name, norm_name, is_primary "
            f"FROM sanctions_aliases WHERE list_name IN ({placeholders})"
        )
        alias_args: list[object] = list(indexed.keys())
        if exact_only:
            alias_sql += " AND length(norm_name) = ?"
            alias_args.append(len(q_norm))
        alias_rows = conn.execute(alias_sql, alias_args).fetchall()

        best_by_entity: dict[tuple[str, str], tuple[str, float, str]] = {}
        for l_name, eid, raw_alias, norm_alias, _is_prim in alias_rows:
            if norm_alias == q_norm or token_sort_key(norm_alias) == q_sorted:
                qual, sc = "exact_match", 1.0
            elif exact_only:
                continue
            else:
                sc = similarity_score(q_norm, norm_alias)
                if sc < threshold:
                    continue
                qual = "possible_match"
            ekey = (l_name, eid)
            prev = best_by_entity.get(ekey)
            if prev is None or sc > prev[1] or (sc == prev[1] and qual == "exact_match"):
                best_by_entity[ekey] = (qual, sc, raw_alias)

        now = time.time()
        age_notes = [
            f"{k.upper()} indexed {int(max(0, now - ts) // 3600)}h ago"
            for k, ts in indexed.items()
        ]
        caveats = [
            f"Local index freshness: {'; '.join(age_notes)} (pass --refresh to re-download).",
            "Arabic, Cyrillic, and CJK names romanize inconsistently across jurisdictions; "
            "aliases cover common variants, but try alternative transliterations before publishing.",
        ]
        if exact_only:
            caveats.append("EXACT-MATCH ONLY (--exact): fuzzy/transliteration scoring was disabled.")
        else:
            caveats.append(f"Fuzzy threshold {threshold}: candidates below it were excluded, and "
                           "matches below 1.0 are marked `possible_match` (verify BirthDate_Year and programme).")

        not_searched = [
            "OFAC non-SDN lists (Sectoral Sanctions Identifications / FSE) and UN/UK national lists unless in EU Consolidated",
            "entities subject to the OFAC 50% ownership rule that are not explicitly designated by name",
        ]
        if failed:
            not_searched.append("lists that failed to index: " + ", ".join(failed.keys()))

        probes = [
            Probe(source=f"{SOURCE}:{k}", endpoint=URLS[k], query=query,
                  params={"normalized": q_norm, "exact_only": exact_only, "threshold": threshold},
                  corpus=f"{k.upper()} consolidated sanctions list (indexed locally)",
                  result_count=sum(1 for (ln, _) in best_by_entity if ln == k),
                  exact_match_supported=True, at=now)
            for k in indexed
        ]

        if not best_by_entity:
            marker = f"sanctions_aliases ({','.join(indexed)}): 0 matches >= {1.0 if exact_only else threshold}"
            if use_cache and cov.is_clean:
                try:
                    store.put(key, SOURCE, [], ttl_s=3600,
                              meta=absence_meta(probes, not_searched, caveats, marker))
                except Exception:
                    pass
            return verified_absence(
                query, cov,
                f"sanctions screening ({', '.join(k.upper() for k in indexed)}) for {query!r}",
                probes=probes, not_searched=not_searched, caveats=caveats)

        # Sort exact_match first, then by score descending
        ranked = sorted(best_by_entity.items(),
                        key=lambda kv: (0 if kv[1][0] == "exact_match" else 1, -kv[1][1], kv[0][0], kv[0][1]))
        results: list[Result] = []
        for (l_name, eid), (qual, sc, matched_alias) in ranked[:50]:
            ent = conn.execute(
                "SELECT primary_name, entity_type, programme, birth_year, country, pub_url, remarks "
                "FROM sanctions_entities WHERE list_name=? AND entity_id=?",
                (l_name, eid),
            ).fetchone()
            if not ent:
                continue
            p_name, e_type, prog, byear, country, pub_url, remarks = ent
            all_aliases = [r[0] for r in conn.execute(
                "SELECT DISTINCT alias_name FROM sanctions_aliases WHERE list_name=? AND entity_id=?",
                (l_name, eid),
            ).fetchall()]
            rid = f"{SOURCE}:{l_name}:{eid}"
            try:
                store.put_record(rid, SOURCE, query, {
                    "list": l_name, "entity_id": eid, "primary_name": p_name,
                    "entity_type": e_type, "programme": prog, "birth_year": byear,
                    "country": country, "pub_url": pub_url, "remarks": remarks,
                    "aliases": all_aliases, "matched_alias": matched_alias,
                    "match_quality": qual, "score": sc,
                })
            except Exception:
                pass

            snip_bits = [b for b in (
                f"{l_name.upper()} #{eid}",
                f"{qual} (score {sc:.2f} on {matched_alias!r})",
                f"programme: {prog}" if prog else "",
                f"born: {byear}" if byear else "",
                f"country: {country}" if country else "",
                e_type,
            ) if b]

            results.append(Result(
                url=pub_url or URLS[l_name],
                title=f"[{l_name.upper()} · {qual.upper()}] {p_name}"
                      + (f" (matched alias: {matched_alias})" if matched_alias != p_name else ""),
                snippet=" | ".join(snip_bits),
                source=SOURCE, engines=[f"{SOURCE}:{l_name}"], index_origin=["own-index"],
                score=sc,
                meta={
                    "record_id": rid,
                    "list": l_name,
                    "entity_id": eid,
                    "primary_name": p_name,
                    "matched_alias": matched_alias,
                    "match_quality": qual,
                    "similarity_score": sc,
                    "programme": prog or None,
                    "birth_year": byear or None,
                    "country": country or None,
                    "entity_type": e_type or None,
                    "publication_url": pub_url or None,
                    "alias_count": len(all_aliases),
                    "verify_note": (None if qual == "exact_match" else
                                    "POSSIBLE MATCH (fuzzy score < 1.0): confirm BirthDate_Year, "
                                    "programme and remarks before attributing a designation."),
                },
            ))
    finally:
        conn.close()

    if use_cache and cov.is_clean:
        try:
            store.put(key, SOURCE, [r.__dict__ for r in results], ttl_s=3600)
        except Exception as e:
            cov.cache_write_refused = str(e)
    return Hit(query=query, coverage=cov, results=results, probes=probes)
