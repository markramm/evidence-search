"""Tests for declarative REST driver (`rest_manifest`), `investigate`, and Phase 2 MCP server."""
from __future__ import annotations

import io
import json
import pathlib
import tempfile

from evidence_search import mcp_server
from evidence_search.cli import EXIT_ACCESS_BLOCKER, EXIT_HIT, main
from evidence_search.core import conformance, investigate, registry
from evidence_search.core import http as core_http
from evidence_search.core.limits import Limiter
from evidence_search.core.results import AccessBlocker, Blocker, Hit, VerifiedAbsence
from evidence_search.core.store import Store


def _kit(tmp_path: pathlib.Path | None = None):
    root = tmp_path or pathlib.Path(tempfile.mkdtemp())
    s = Store(root / "next_features.db")
    return s, Limiter(s)


def test_rest_manifest_driver_and_datagov_catalog_plugin(tmp_path, monkeypatch, capsys):
    """Generic `'driver': 'rest'` powers `datagov_catalog` + CKAN dotted paths + `plugin init --driver rest`."""
    from evidence_search.sources import rest_manifest

    v1_hit = json.dumps({
        "results": [{
            "slug": "federal-detention-contracts-2026",
            "dcat": {"title": "Federal Detention Contracts Dataset"},
            "description": "Quarterly facility award table",
            "organization": {"title": "Department of Homeland Security"},
        }],
        "sort": "relevance",
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (v1_hit, None))

    rc = main(["--json", "datagov_catalog", "detention contracts", "--limit", "5"])
    assert rc == EXIT_HIT
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["url"] == "https://catalog.data.gov/dataset/federal-detention-contracts-2026"
    assert "Federal Detention Contracts Dataset" in payload["results"][0]["title"]
    assert "Department of Homeland Security" in payload["results"][0]["snippet"]

    # Also verify dotted `rows_path` / `total_path` (e.g., CKAN style) via rest_manifest.run_manifest
    ckan_hit = json.dumps({
        "success": True,
        "result": {
            "count": 7,
            "results": [{"name": "ckan-ds", "title": "CKAN Dataset", "notes": "Note body"}],
        },
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (ckan_hit, None))
    s, L = _kit(tmp_path)
    ck_out = rest_manifest.run_manifest(
        {
            "name": "ckan_probe",
            "endpoint": "https://ckan.example.gov/api/3/action/package_search",
            "rows_path": "result.results",
            "total_path": "result.count",
            "title_fields": ["title"],
            "item_url_template": "https://ckan.example.gov/dataset/{name}",
        },
        "audit",
        store=s, limiter=L, use_cache=False,
    )
    assert isinstance(ck_out, Hit)
    assert ck_out.results[0].meta["total_matches"] == 7
    assert ck_out.results[0].url == "https://ckan.example.gov/dataset/ckan-ds"

    # HTTP-200 error payload must become AccessBlocker, never VerifiedAbsence
    monkeypatch.setattr(
        core_http,
        "fetch",
        lambda *a, **k: (json.dumps({"success": False, "error": {"message": "Bad query"}}), None),
    )
    spec = registry.get_spec("datagov_catalog")
    assert spec is not None and spec.run is not None
    blocked = spec.run("bad", store=s, limiter=L, use_cache=False)
    assert isinstance(blocked, AccessBlocker)

    # Empty envelope returns VerifiedAbsence
    monkeypatch.setattr(
        core_http,
        "fetch",
        lambda *a, **k: (json.dumps({"results": [], "sort": "relevance"}), None),
    )
    abs_out = spec.run("nothinghere", store=s, limiter=L, use_cache=False)
    assert isinstance(abs_out, VerifiedAbsence)

    # Conformance harness passes cleanly on datagov_catalog
    rep = conformance.check_source("datagov_catalog")
    assert rep["passed"] is True, rep

    # `plugin init --driver rest` scaffolds and validates a custom REST plugin
    pdir = tmp_path / "rest_plugins"
    monkeypatch.setenv("EVIDENCE_PLUGINS_DIR", str(pdir))
    try:
        rc_init = main([
            "plugin", "init", "custom_open_catalog",
            "--driver", "rest",
            "--endpoint", "https://data.example.gov/api/search",
            "--rows-path", "results",
            "--total-path", "count",
        ])
        assert rc_init == 0
        capsys.readouterr()
        assert (pdir / "custom_open_catalog.json").exists()
        assert conformance.check_source("custom_open_catalog")["passed"] is True
    finally:
        registry.REGISTRY.pop("custom_open_catalog", None)


def test_investigate_cross_source_pivots_and_cli(tmp_path, monkeypatch, capsys):
    """`investigate` extracts CIK/EIN/UEI/domains and runs secondary deep-dive pivots."""
    from evidence_search.sources import crtsh, nonprofit, sec, wayback

    def _mock_dispatch(source_name, query, *, limit=5, store=None, limiter=None, use_cache=True, extra=None):
        s_dummy, _ = _kit(tmp_path)
        from evidence_search.core.results import Coverage, Probe, Result, verified_absence

        cov = Coverage(queried=[source_name], responsive=[source_name])
        pr = Probe(source=source_name, query=query, corpus=source_name)
        if source_name == "usaspending":
            return Hit(
                query=query,
                coverage=cov,
                results=[Result(
                    url="https://www.usaspending.gov/award/CONT_001",
                    title="ACME CORP — $5,000,000",
                    snippet="UEI: ABCDEF123456 | PIID 70CDCR26FR0000001",
                    source="usaspending",
                    meta={"recipient_uei": "ABCDEF123456", "award_id": "CONT_001"},
                )],
                probes=[pr],
            )
        if source_name == "sec":
            return Hit(
                query=query,
                coverage=cov,
                results=[Result(
                    url="https://www.sec.gov/edgar/browse/?CIK=0001070985",
                    title="ACME CORP 10-K",
                    snippet="EIN 12-3456789 | vendor https://acme-corp.example.com/gov",
                    source="sec",
                    meta={"ciks": ["0001070985"], "website": "https://acme-corp.example.com"},
                )],
                probes=[pr],
            )
        if source_name == "nonprofit":
            return verified_absence(query, cov, f"nonprofit ({query})", probes=[pr])
        if source_name == "fara":
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=["fara"], errored={"fara": "blocked"}),
                mechanism=Blocker.SERVER_ERROR,
                url="https://efile.fara.gov/",
                detail="simulated upstream 503",
            )
        return verified_absence(query, cov, f"{source_name} ({query})", probes=[pr])

    monkeypatch.setattr(investigate, "run_single_source", _mock_dispatch)

    ein_calls: list[str] = []
    cik_calls: list[str] = []
    crt_calls: list[str] = []

    monkeypatch.setattr(
        nonprofit,
        "by_ein",
        lambda ein, **kw: (
            ein_calls.append(ein)
            or Hit(
                query=ein,
                coverage=__import__("evidence_search.core.results", fromlist=["Coverage"]).Coverage(
                    queried=["nonprofit"], responsive=["nonprofit"]
                ),
                results=[],
                probes=[],
            )
        ),
    )
    monkeypatch.setattr(
        sec,
        "submissions",
        lambda cik, **kw: (
            cik_calls.append(cik)
            or Hit(
                query=cik,
                coverage=__import__("evidence_search.core.results", fromlist=["Coverage"]).Coverage(
                    queried=["sec"], responsive=["sec"]
                ),
                results=[],
                probes=[],
            )
        ),
    )
    monkeypatch.setattr(
        crtsh,
        "search",
        lambda dom, **kw: (
            crt_calls.append(dom)
            or Hit(
                query=dom,
                coverage=__import__("evidence_search.core.results", fromlist=["Coverage"]).Coverage(
                    queried=["crtsh"], responsive=["crtsh"]
                ),
                results=[],
                probes=[],
            )
        ),
    )
    _ = wayback

    s, L = _kit(tmp_path)
    report = investigate.run_investigation(
        "ACME CORP",
        sources=["usaspending", "sec", "nonprofit", "fara"],
        store=s,
        limiter=L,
        use_cache=False,
    )
    assert report["pivots"]["cik"] == ["0001070985"]
    assert report["pivots"]["ein"] == ["12-3456789"]
    assert report["pivots"]["uei"] == ["ABCDEF123456"]
    assert "70CDCR26FR0000001" in report["pivots"]["award_ids"]
    assert report["pivots"]["domains"] == ["acme-corp.example.com"]
    assert cik_calls == ["0001070985"]
    assert ein_calls == ["12-3456789"]
    assert crt_calls == ["acme-corp.example.com"]
    assert "fara" in report["summary"]["incomplete_sources"]
    assert report["summary"]["is_complete"] is False

    # CLI `investigate` formats human output and sets EXIT_HIT when hits exist
    monkeypatch.setenv("EVIDENCE_DB", str(tmp_path / "cli_inv.db"))
    rc = main(["investigate", "ACME CORP", "--sources", "usaspending,sec,nonprofit,fara"])
    out = capsys.readouterr().out
    assert rc == EXIT_HIT
    assert "Discovered identifier pivots:" in out
    assert "0001070985" in out

    # Blocked/incomplete-only investigation returns EXIT_ACCESS_BLOCKER, not EXIT_VERIFIED_ABSENCE
    rc_blk = main(["--json", "investigate", "ACME CORP", "--sources", "fara"])
    assert rc_blk == EXIT_ACCESS_BLOCKER
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["summary"]["is_complete"] is False


def test_mcp_server_jsonrpc_and_cli_tools(tmp_path, monkeypatch, capsys):
    """Phase 2 MCP server handles JSON-RPC lifecycle, tool dispatch, and stdio transport."""
    s, L = _kit(tmp_path)

    # 1. Initialize + ping + notifications + unknown method
    init_resp = mcp_server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert init_resp["result"]["serverInfo"]["name"] == "evidence-search"

    assert mcp_server.handle_jsonrpc_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert mcp_server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}
    unknown = mcp_server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 3, "method": "no/such/method"})
    assert unknown["error"]["code"] == -32601

    # 2. tools/list lists all 6 MCP tools
    listed = mcp_server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    tool_names = {t["name"] for t in listed["result"]["tools"]}
    assert {
        "evidence_sources",
        "evidence_search",
        "evidence_investigate",
        "evidence_extract",
        "evidence_record",
        "evidence_limits",
    } == tool_names

    # 3. tools/call on evidence_sources, evidence_extract, evidence_limits, evidence_search
    src_call = mcp_server.handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "evidence_sources", "arguments": {"source": "openalex"}}},
        store=s, limiter=L,
    )
    assert src_call["result"]["isError"] is False
    assert json.loads(src_call["result"]["content"][0]["text"])["name"] == "openalex"

    sample_file = tmp_path / "brief.txt"
    sample_file.write_text("Contract 70CDCR26C001 awarded with UEI XYZ987654321 and EIN 98-7654321.")
    ex_call = mcp_server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "evidence_extract", "arguments": {"target": str(sample_file), "grep": ["70CDCR26C001"], "ids": True}},
        },
        store=s, limiter=L,
    )
    ex_data = json.loads(ex_call["result"]["content"][0]["text"])
    assert ex_data["matches"][0]["pattern"] == "70CDCR26C001"

    monkeypatch.setattr(
        core_http,
        "fetch",
        lambda *a, **k: (json.dumps({"articles": [{"url": "https://ex.org/1", "title": "MCP GDELT Hit", "domain": "ex.org"}]}), None),
    )
    search_call = mcp_server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "evidence_search", "arguments": {"source": "gdelt", "query": "test", "use_cache": False}},
        },
        store=s, limiter=L,
    )
    s_data = json.loads(search_call["result"]["content"][0]["text"])
    assert s_data["outcome"] == "Hit"
    assert s_data["results"][0]["title"] == "MCP GDELT Hit"

    # 4. serve_stdio handles newline-delimited JSON-RPC + parse errors cleanly
    in_buf = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"}) + "\n"
        + "{broken json\n"
    )
    out_buf = io.StringIO()
    assert mcp_server.serve_stdio(in_buf, out_buf, store=s, limiter=L) == 0
    lines = [json.loads(line) for line in out_buf.getvalue().splitlines() if line.strip()]
    assert lines[0]["id"] == 10 and lines[0]["result"] == {}
    assert lines[1]["error"]["code"] == -32700

    # 5. CLI `mcp --list-tools` and `mcp --call evidence_limits`
    assert main(["mcp", "--list-tools"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 6
    assert main(["mcp", "--call", "evidence_limits", "--args", '{"source": "web"}']) == 0
    lim_out = json.loads(capsys.readouterr().out)
    assert lim_out["isError"] is False
    assert json.loads(lim_out["content"][0]["text"])["limits"][0]["source"] == "web"


def test_brokers_per_state_incremental_indexing_and_refresh(tmp_path, monkeypatch):
    """Querying CA first must not cause subsequent VT or all-state searches to falsely miss VT."""
    from evidence_search.sources import brokers

    ca_csv = "Broker Name,State,Registration Number\nCA Broker One,CA,CA-10\n"
    vt_csv_v1 = "Broker Name,State,Registration Number\nVT Broker One,VT,VT-10\n"
    vt_csv_v2 = "Broker Name,State,Registration Number\nVT Broker Refreshed,VT,VT-99\n"
    vt_version = [vt_csv_v1]
    seen_fetches: list[str] = []

    def _fake_fetch(url, *, source, query="", **kw):
        seen_fetches.append(url)
        if "cppa.ca.gov" in url:
            return ca_csv, None
        if "sos.vermont.gov" in url:
            return vt_version[0], None
        return "Broker Name,State,Registration Number\n", None

    monkeypatch.setattr(brokers, "fetch", _fake_fetch)
    bdir = tmp_path / "brok_inc"
    s, L = _kit(tmp_path)

    # 1. Index CA only
    ca_hit = brokers.search("CA Broker", state="CA", store=s, limiter=L, cache_dir=bdir, use_cache=True)
    assert isinstance(ca_hit, Hit) and len(ca_hit.results) == 1
    assert any("cppa.ca.gov" in u for u in seen_fetches)

    # 2. Query VT next: must fetch & index VT instead of treating non-empty DB as complete
    vt_hit = brokers.search("VT Broker One", state="VT", store=s, limiter=L, cache_dir=bdir, use_cache=True)
    assert isinstance(vt_hit, Hit) and vt_hit.results[0].meta["reg_number"] == "VT-10"

    # 3. Refresh VT with updated upstream export
    vt_version[0] = vt_csv_v2
    vt_ref = brokers.search("VT Broker Refreshed", state="VT", refresh=True, store=s, limiter=L, cache_dir=bdir, use_cache=True)
    assert isinstance(vt_ref, Hit) and vt_ref.results[0].meta["reg_number"] == "VT-99"


def test_put_record_coverage_across_drivers_and_bulk_sources(tmp_path, monkeypatch):
    """socrata, arcgis, rest_manifest, crtsh, fara, icij, brokers, and fec all populate Store records."""
    from evidence_search.sources import arcgis, brokers, crtsh, fara, fec, icij, rest_manifest, socrata

    s, L = _kit(tmp_path)

    # socrata
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps([{"id": "SOC-1", "vendor": "V1"}]), None))
    monkeypatch.setattr(socrata, "fetch", lambda *a, **k: (json.dumps([{"id": "SOC-1", "vendor": "V1"}]), None))
    soc_out = socrata.query("data.example.gov", "abcd-1234", "V1", store=s, limiter=L, use_cache=False)
    soc_rid = soc_out.results[0].meta["record_id"]
    assert s.get_record(soc_rid)["payload"]["vendor"] == "V1"

    # arcgis
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps({"features": [{"attributes": {"OBJECTID": 77, "OwnerName": "ACME"}}]}), None))
    arc_out = arcgis.query("https://gis.example.gov/FeatureServer/0", "ACME", field="OwnerName", store=s, limiter=L, use_cache=False)
    arc_rid = arc_out.results[0].meta["record_id"]
    assert s.get_record(arc_rid)["payload"]["OwnerName"] == "ACME"

    # rest_manifest
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps({"results": [{"slug": "ds-1", "title": "Dataset 1"}]}), None))
    rest_out = rest_manifest.run_manifest({"name": "rest_p", "endpoint": "https://ex.gov/api"}, "ds", store=s, limiter=L, use_cache=False)
    rest_rid = rest_out.results[0].meta["record_id"]
    assert s.get_record(rest_rid)["payload"]["title"] == "Dataset 1"

    # crtsh
    crt_row = {"id": 9001, "common_name": "sub.ex.gov", "name_value": "sub.ex.gov"}
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps([crt_row]), None))
    monkeypatch.setattr(crtsh, "fetch", lambda *a, **k: (json.dumps([crt_row]), None))
    crt_out = crtsh.search("sub.ex.gov", store=s, limiter=L, use_cache=False)
    assert s.get_record(crt_out.results[0].meta["record_id"])["payload"]["id"] == 9001

    # fara
    fdir = tmp_path / "f_rec"
    fara.index_fara_rows(fara.conn_for(fdir), fara.parse_fara_payload('"Foreign Principal","Registration Number","Registrant Name"\n"Gov X","555","Lobby Y"\n'))
    f_out = fara.search("Gov X", store=s, limiter=L, cache_dir=fdir, use_cache=False)
    assert s.get_record(f_out.results[0].meta["record_id"])["payload"]["reg_number"] == "555"

    # icij
    idir = tmp_path / "i_rec"
    ns, rs = icij.parse_icij_payload(json.dumps({"nodes": [{"node_id": "N9", "node_type": "entity", "name": "OFFSHORE CO"}], "relationships": []}))
    icij.index_icij_data(icij.conn_for(idir), ns, rs)
    i_out = icij.search("OFFSHORE", store=s, limiter=L, cache_dir=idir, use_cache=False)
    assert s.get_record(i_out.results[0].meta["record_id"])["payload"]["node_id"] == "N9"

    # brokers & fec
    bdir = tmp_path / "b_rec"
    brokers.index_brokers(brokers.conn_for(bdir), brokers.parse_brokers_payload("Broker Name,State,Registration Number\nData Co,CA,CA-88\n"), state="CA")
    b_out = brokers.search("Data Co", state="CA", store=s, limiter=L, cache_dir=bdir, use_cache=False)
    assert s.get_record(b_out.results[0].meta["record_id"])["payload"]["reg_number"] == "CA-88"

    fedir = tmp_path / "fec_rec"
    conn_fec = fec.conn_for(fedir)
    fec._index_rows(conn_fec, "cm", 2008, [{"CMTE_ID": "C00999999", "CMTE_NM": "TEST PAC", "CMTE_ST": "DC", "CMTE_TP": "Q", "CMTE_PTY_AFFILIATION": "UNK", "CAND_ID": ""}])
    fec._mark_indexed(conn_fec, "cm", 2008, 1)
    conn_fec.close()
    cm_out = fec.lookup_committee("TEST PAC", 2008, 2008, store=s, limiter=L, cache_dir=fedir, use_cache=False)
    assert s.get_record(cm_out.results[0].meta["record_id"])["payload"]["committee_id"] == "C00999999"


def test_run_single_source_submodes_and_fec_comma_cycles(tmp_path, monkeypatch):
    from evidence_search.sources import fec, nonprofit, sec

    assert fec.parse_cycle_range("2006,2008") == (2006, 2008)
    assert fec.parse_cycle_range("2012, 2008, 2010") == (2008, 2012)

    s, L = _kit(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(nonprofit, "by_ein", lambda ein, **kw: called.append(f"ein:{ein}") or VerifiedAbsence(query=ein, coverage=__import__("evidence_search.core.results", fromlist=["Coverage"]).Coverage(queried=["nonprofit"], responsive=["nonprofit"]), searched="nonprofit"))
    monkeypatch.setattr(sec, "submissions", lambda comp, **kw: called.append(f"sec:{comp}") or VerifiedAbsence(query=comp, coverage=__import__("evidence_search.core.results", fromlist=["Coverage"]).Coverage(queried=["sec"], responsive=["sec"]), searched="sec"))

    investigate.run_single_source("nonprofit", "x", extra={"ein": "52-2166327"}, store=s, limiter=L, use_cache=False)
    investigate.run_single_source("sec", "x", extra={"cik": "CXW"}, store=s, limiter=L, use_cache=False)
    assert called == ["ein:52-2166327", "sec:CXW"]

    # CLI sec --cik alias parses cleanly
    import argparse
    orig_parse = argparse.ArgumentParser.parse_args
    captured_ns = []
    class _Stop(Exception):
        pass
    def _spy(self, args=None, namespace=None):
        captured_ns.append(orig_parse(self, args, namespace))
        raise _Stop()
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", _spy)
    try:
        main(["sec", "--cik", "CXW", "--forms", "8-K"])
    except _Stop:
        pass
    assert captured_ns[0].company == "CXW"


def test_skill_md_command_examples_parse_cleanly(monkeypatch):
    """Every `$CS ...` example in SKILL.md must parse without argparse usage errors."""
    import argparse
    import re
    import shlex

    skill_path = pathlib.Path(__file__).resolve().parent.parent / "skills" / "evidence-search-usage" / "SKILL.md"
    lines = skill_path.read_text().splitlines()
    cmd_lines = [ln.strip() for ln in lines if ln.strip().startswith("$CS ")]
    assert len(cmd_lines) >= 25

    orig_parse = argparse.ArgumentParser.parse_args

    class _ParsedOK(Exception):
        pass

    def _spy_parse(self, args=None, namespace=None):
        ns = orig_parse(self, args, namespace)
        raise _ParsedOK(ns)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", _spy_parse)

    for raw in cmd_lines:
        body = raw[len("$CS "):].split(" | ", 1)[0].replace(">/dev/null", "")
        body = body.replace("[--live]", "--live")
        tokens = shlex.split(body, comments=True)
        tokens = [re.sub(r"<[^>]+>", "stub", t) for t in tokens]
        try:
            main(tokens)
        except _ParsedOK:
            continue
        except SystemExit as e:
            raise AssertionError(f"SKILL.md example failed CLI parse ({raw!r}): exit {e.code}") from e

