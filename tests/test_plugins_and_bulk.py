"""Tests for Wave 6-7 plugins, generic drivers, declarative manifests, and bulk caching."""
from __future__ import annotations

import json
import pathlib
import tempfile

from evidence_search.cli import EXIT_HIT, main
from evidence_search.core import conformance, registry
from evidence_search.core import http as core_http
from evidence_search.core.limits import Limiter
from evidence_search.core.results import AccessBlocker, Blocker, Hit, VerifiedAbsence
from evidence_search.core.store import Store


def _kit(tmp_path: pathlib.Path | None = None):
    root = tmp_path or pathlib.Path(tempfile.mkdtemp())
    s = Store(root / "plugins_test.db")
    return s, Limiter(s)


def test_fara_foreign_principals_and_registrants(tmp_path):
    from evidence_search.sources import fara

    csv_fixture = (
        '"Foreign Principal","Foreign Principal Registration Date","Country/Location Represented",'
        '"Registration Number","Registrant Date","Registrant Name","Address 1","City","State"\n'
        '"Ministry of Tourism of El Salvador","03/15/2025","EL SALVADOR","7391","03/10/2025",'
        '"Damián Merlo Strategies LLC","1700 K St NW","Washington","DC"\n'
        '"Embassy of Japan","01/10/2020","JAPAN","6500","01/05/2020",'
        '"Akin Gump Strauss Hauer & Feld LLP","2001 K St NW","Washington","DC"\n'
    )
    rows = fara.parse_fara_payload(csv_fixture)
    assert len(rows) == 2

    cache_dir = tmp_path / "fara_bulk"
    conn = fara.conn_for(cache_dir)
    fara.index_fara_rows(conn, rows)

    s, L = _kit(tmp_path)
    out = fara.search(country="EL SALVADOR", store=s, limiter=L, cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit)
    assert len(out.results) == 1
    assert out.results[0].meta["reg_number"] == "7391"
    assert "Ministry of Tourism of El Salvador" in out.results[0].title

    abs_out = fara.search(country="NARNIA", store=s, limiter=L, cache_dir=cache_dir, use_cache=False)
    assert isinstance(abs_out, VerifiedAbsence)
    assert any("lda" in ns.lower() for ns in abs_out.not_searched)


def test_wayback_cdx_captures_and_digest_tracking(tmp_path, monkeypatch):
    from evidence_search.sources import wayback

    cdx_json = json.dumps([
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["20260312140000", "https://vendor.example.com/services", "text/html", "200", "SHA1DIGESTAAAA1111", "18420"],
        ["20260402093000", "https://vendor.example.com/services", "text/html", "200", "SHA1DIGESTBBBB2222", "12100"],
    ])
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (cdx_json, None))
    monkeypatch.setattr(wayback, "fetch", lambda *a, **k: (cdx_json, None))
    s, L = _kit(tmp_path)
    out = wayback.search("https://vendor.example.com/services", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert len(out.results) == 2
    assert out.results[0].meta["distinct_digest_count"] == 2
    assert out.results[0].meta["raw_archive_url"] == "https://web.archive.org/web/20260312140000id_/https://vendor.example.com/services"


def test_openalex_works_and_retraction_warning(tmp_path, monkeypatch):
    from evidence_search.sources import openalex

    oa_payload = json.dumps({
        "meta": {"count": 1, "page": 1, "per_page": 25},
        "results": [{
            "id": "https://openalex.org/W4390001111",
            "doi": "https://doi.org/10.1000/retracted.2024",
            "title": "Biomechanical Reaction Time Under Simulated Stress",
            "publication_year": 2024,
            "publication_date": "2024-05-12",
            "cited_by_count": 19,
            "is_retracted": True,
            "primary_location": {"source": {"display_name": "Journal of Applied Policing"}},
            "open_access": {"oa_url": "https://example.org/paper.pdf"},
            "authorships": [{
                "author": {"display_name": "John Doe"},
                "institutions": [{"display_name": "Force Science Institute"}],
            }],
            "grants": [{"funder_display_name": "National Institute of Justice", "award_id": "2022-IJ-CX-0001"}],
        }],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (oa_payload, None))
    s, L = _kit(tmp_path)
    out = openalex.search("Biomechanical Reaction Time", retracted_only=True,
                          store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    r = out.results[0]
    assert r.title.startswith("[RETRACTED]")
    assert r.meta["is_retracted"] is True
    assert "RETRACTED WORK" in r.meta["entity_caveat"]
    assert "Force Science Institute" in r.meta["institutions"]


def test_socrata_and_arcgis_generic_drivers(tmp_path, monkeypatch):
    from evidence_search.sources import arcgis, socrata

    soc_rows = json.dumps([
        {"vendor_name": "ACME DETENTION SERVICES LLC", "contract_amount": "14500000", "agency": "DOC"},
    ])
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (soc_rows, None))
    monkeypatch.setattr(socrata, "fetch", lambda *a, **k: (soc_rows, None))
    s, L = _kit(tmp_path)
    out = socrata.query("data.cityofnewyork.us", "abcd-1234", "ACME DETENTION",
                        store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert "ACME DETENTION SERVICES LLC" in out.results[0].title

    # ArcGIS HTTP-200 error envelope must become AccessBlocker(SERVER_ERROR), NEVER VerifiedAbsence
    arc_err = json.dumps({"error": {"code": 400, "message": "Invalid or missing input parameters."}})
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (arc_err, None))
    s2 = Store(tmp_path / "arc2.db")
    blocked = arcgis.query("https://gis.mt.gov/arcgis/rest/services/Parcels/FeatureServer/0",
                           "BAD", field="OwnerName", store=s2, limiter=Limiter(s2), use_cache=False)
    assert isinstance(blocked, AccessBlocker)
    assert blocked.mechanism is Blocker.SERVER_ERROR
    assert "Invalid or missing input parameters" in blocked.detail

    # ArcGIS valid hit
    arc_ok = json.dumps({
        "features": [
            {"attributes": {"PARCEL_ID": "04-1234-56", "OwnerName": "YELLOWSTONE HOLDINGS LLC", "CountyName": "Gallatin"}}
        ]
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (arc_ok, None))
    s3 = Store(tmp_path / "arc3.db")
    hit = arcgis.query("https://gis.mt.gov/arcgis/rest/services/Parcels/FeatureServer/0",
                       "YELLOWSTONE HOLDINGS", field="OwnerName", store=s3, limiter=Limiter(s3), use_cache=False)
    assert isinstance(hit, Hit)
    assert "YELLOWSTONE HOLDINGS LLC" in hit.results[0].title


def test_declarative_manifest_plugin_and_conformance_harness(tmp_path, monkeypatch, capsys):
    """A 15-line JSON manifest in EVIDENCE_PLUGINS_DIR registers as a first-class
    source plugin, passes `plugin check`, and runs via `plugin run`."""
    from evidence_search.sources import socrata

    pdir = tmp_path / "plugins"
    pdir.mkdir()
    manifest = {
        "name": "nyc_contracts_test",
        "driver": "socrata",
        "domain": "data.cityofnewyork.us",
        "dataset_id": "nycc-9999",
        "search_field": "vendor_name",
        "title_fields": ["vendor_name", "agency", "amount"],
        "summary": "NYC Checkbook city procurement contracts (Socrata plugin)",
        "corpus": "NYC Checkbook Open Data contracts dataset (nycc-9999).",
        "not_searched": [
            "Subcontracts not reported to NYC Checkbook",
            "State-level NY OSC contracts",
        ],
        "caveats": ["Vendor names are uppercase legal strings."],
    }
    (pdir / "nyc_contracts_test.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("EVIDENCE_PLUGINS_DIR", str(pdir))
    try:
        registry.load_plugins(pdir, force=True)

        spec = registry.get_spec("nyc_contracts_test")
        assert spec is not None
        assert spec.driver == "socrata"

        rep_plugin = conformance.check_source("nyc_contracts_test")
        assert rep_plugin["passed"] is True, rep_plugin
        rep_oa = conformance.check_source("openalex")
        assert rep_oa["passed"] is True, rep_oa

        soc_rows = json.dumps([{"vendor_name": "METRO SERVICES INC", "agency": "DHS", "amount": "2500000"}])
        monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (soc_rows, None))
        monkeypatch.setattr(socrata, "fetch", lambda *a, **k: (soc_rows, None))
        rc = main(["plugin", "run", "nyc_contracts_test", "METRO SERVICES"])
        out = capsys.readouterr().out
        assert rc == EXIT_HIT
        assert "METRO SERVICES INC" in out
    finally:
        registry.REGISTRY.pop("nyc_contracts_test", None)


def test_kev_and_crtsh_cyber_pack(tmp_path, monkeypatch):
    from evidence_search.sources import crtsh, kev

    kev_json = json.dumps({
        "title": "CISA KEV",
        "vulnerabilities": [{
            "cveID": "CVE-2024-21887",
            "vendorProject": "Ivanti",
            "product": "Connect Secure",
            "vulnerabilityName": "Ivanti Connect Secure Command Injection",
            "dateAdded": "2024-01-12",
            "dueDate": "2024-01-31",
            "knownRansomwareCampaignUse": "Known",
            "requiredAction": "Apply mitigations per vendor instructions",
        }],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (kev_json, None))
    s, L = _kit(tmp_path)
    out = kev.search("Ivanti", ransomware_only=True, store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.results[0].meta["cve_id"] == "CVE-2024-21887"
    assert out.results[0].meta["due_date"] == "2024-01-31"

    crt_json = json.dumps([
        {"id": 1001, "common_name": "*.contractor.example.gov",
         "name_value": "*.contractor.example.gov\nice-portal.contractor.example.gov",
         "issuer_name": "C=US, O=DigiCert Inc", "not_before": "2026-01-01", "not_after": "2027-01-01"},
    ])
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (crt_json, None))
    monkeypatch.setattr(crtsh, "fetch", lambda *a, **k: (crt_json, None))
    s2 = Store(tmp_path / "crt.db")
    c_out = crtsh.search("%.contractor.example.gov", store=s2, limiter=Limiter(s2), use_cache=False)
    assert isinstance(c_out, Hit)
    assert "ice-portal.contractor.example.gov" in c_out.results[0].meta["san_domains"]


def test_epa_openfda_and_cfpb_enforcement_pack(tmp_path, monkeypatch):
    from evidence_search.sources import cfpb, epa, openfda

    epa_json = json.dumps({
        "Results": {
            "QueryRows": "1",
            "Facilities": [{
                "RegistryID": "110000123456",
                "FacName": "GEO CHEMICAL PLANT",
                "FacCity": "Baton Rouge", "FacState": "LA",
                "FacQtrsWithNC": "8", "FacFormalActionCount": "2",
                "FacTotalPenalties": "$125,000",
            }],
        }
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (epa_json, None))
    s1 = Store(tmp_path / "epa.db")
    epa_hit = epa.search("GEO CHEMICAL", state="LA", store=s1, limiter=Limiter(s1), use_cache=False)
    assert isinstance(epa_hit, Hit)
    assert epa_hit.results[0].meta["qtrs_with_noncompliance"] == 8
    assert "SIGNIFICANT COMPLIANCE HISTORY" in epa_hit.results[0].meta["entity_caveat"]

    fda_json = json.dumps({
        "meta": {"results": {"total": 3}},
        "results": [{
            "recall_number": "Z-0444-2026",
            "recalling_firm": "Baxter Healthcare",
            "product_description": "Sigma Spectrum Infusion Pump",
            "reason_for_recall": "Software alarm fault",
            "event_date_initiated": "20260201",
        }],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (fda_json, None))
    s2 = Store(tmp_path / "fda.db")
    fda_hit = openfda.search("Infusion Pump", firm="Baxter Healthcare", store=s2, limiter=Limiter(s2), use_cache=False)
    assert isinstance(fda_hit, Hit)
    assert fda_hit.results[0].meta["total_matches"] == 3

    cfpb_json = json.dumps({
        "hits": {
            "total": {"value": 12},
            "hits": [{
                "_id": "889900",
                "_source": {
                    "complaint_id": "889900", "company": "SECURUS TECHNOLOGIES",
                    "product": "Prepaid card", "issue": "Unexpected fees",
                    "date_received": "2026-03-04", "state": "TX",
                },
            }],
        }
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (cfpb_json, None))
    s3 = Store(tmp_path / "cfpb.db")
    cfpb_hit = cfpb.search("prison fees", company="SECURUS TECHNOLOGIES", store=s3, limiter=Limiter(s3), use_cache=False)
    assert isinstance(cfpb_hit, Hit)
    assert cfpb_hit.results[0].meta["total_matches"] == 12


def test_icij_brokers_and_gdelt_roadmap_pack(tmp_path, monkeypatch):
    from evidence_search.sources import brokers, gdelt, icij

    icij_payload = json.dumps({
        "nodes": [
            {"node_id": "1001", "node_type": "entity", "name": "ALDERAN HOLDINGS LTD",
             "jurisdiction": "British Virgin Islands", "source_dataset": "Pandora Papers"},
            {"node_id": "2001", "node_type": "officer", "name": "Johnathan Doe",
             "jurisdiction": "United States", "source_dataset": "Pandora Papers"},
        ],
        "relationships": [
            {"start_id": "2001", "rel_type": "beneficial_owner_of", "end_id": "1001"},
        ],
    })
    nodes, rels = icij.parse_icij_payload(icij_payload)
    icij_dir = tmp_path / "icij_db"
    icij.index_icij_data(icij.conn_for(icij_dir), nodes, rels)
    s1 = Store(tmp_path / "icij_s.db")
    ic_hit = icij.search("Alderan Holdings", store=s1, limiter=Limiter(s1), cache_dir=icij_dir, use_cache=False)
    assert isinstance(ic_hit, Hit)
    assert any("Johnathan Doe" in c for c in ic_hit.results[0].meta["connections"])

    brok_csv = (
        "Broker Name,DBA,State,Registration Number,Website\n"
        '"Acxiom LLC","LiveRamp Insights",CA,CA-DB-2026-01,https://acxiom.com\n'
    )
    b_rows = brokers.parse_brokers_payload(brok_csv, default_state="CA")
    b_dir = tmp_path / "brokers_db"
    brokers.index_brokers(brokers.conn_for(b_dir), b_rows, state="CA")
    s2 = Store(tmp_path / "brok_s.db")
    b_hit = brokers.search("LiveRamp Insights LLC", state="CA", store=s2, limiter=Limiter(s2), cache_dir=b_dir, use_cache=False)
    assert isinstance(b_hit, Hit)
    assert b_hit.results[0].meta["reg_number"] == "CA-DB-2026-01"

    gdelt_json = json.dumps({
        "articles": [{
            "url": "https://example.org/border-contract",
            "title": "State awards surveillance contract",
            "domain": "example.org",
            "seendate": "20260401T080000Z",
            "sourcecountry": "United States",
            "language": "English",
        }]
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (gdelt_json, None))
    s3 = Store(tmp_path / "gdelt_s.db")
    gd_hit = gdelt.search("surveillance contract", country="US", store=s3, limiter=Limiter(s3), use_cache=False)
    assert isinstance(gd_hit, Hit)
    assert gd_hit.results[0].meta["domain"] == "example.org"


def test_shipped_manifests_dynamic_cli_plugin_init_and_all_checks(tmp_path, monkeypatch, capsys):
    arc_ok = json.dumps({
        "features": [{"attributes": {"OwnerName": "BIGHORN RANCH LLC", "CountyName": "Gallatin", "PARCELID": "06-1111"}}],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (arc_ok, None))
    rc = main(["montana_cadastral", "BIGHORN RANCH"])
    out = capsys.readouterr().out
    assert rc == EXIT_HIT
    assert "BIGHORN RANCH LLC" in out

    pdir = tmp_path / "scaffolded"
    monkeypatch.setenv("EVIDENCE_PLUGINS_DIR", str(pdir))
    try:
        rc_init = main([
            "plugin", "init", "tx_contracts_probe",
            "--driver", "socrata",
            "--domain", "data.texas.gov",
            "--dataset", "txtx-1234",
            "--search-field", "vendor_name",
        ])
        out_init = capsys.readouterr().out
        assert rc_init == 0
        assert (pdir / "tx_contracts_probe.json").exists()
        assert "positive_fixture_ok" in out_init
        assert "empty_fixture_ok" in out_init

        reports = conformance.check_all_sources()
        assert len(reports) >= 31
        failed = [r for r in reports if not r["passed"]]
        assert not failed, f"Failed conformance checks: {failed}"
    finally:
        registry.REGISTRY.pop("tx_contracts_probe", None)


def test_bulk_sources_cache_replay_and_probe_completeness(tmp_path):
    """FARA, ICIJ, and brokers populate Probe metadata and replay cached Hits/VerifiedAbsences."""
    from evidence_search.sources import brokers, fara, icij

    # FARA
    fara_dir = tmp_path / "fara_c"
    f_rows = fara.parse_fara_payload(
        '"Foreign Principal","Country/Location Represented","Registration Number","Registrant Name"\n'
        '"Alpha Principal","CANADA","1234","Alpha Registrant LLC"\n'
    )
    fara.index_fara_rows(fara.conn_for(fara_dir), f_rows)
    s_f = Store(tmp_path / "f.db")
    f1 = fara.search("Alpha", store=s_f, limiter=Limiter(s_f), cache_dir=fara_dir, use_cache=True)
    assert isinstance(f1, Hit)
    assert f1.probes[0].endpoint == fara.FARA_PRINCIPALS_URL
    assert f1.probes[0].result_count == 1
    assert f1.probes[0].at > 0
    f2 = fara.search("Alpha", store=s_f, limiter=Limiter(s_f), cache_dir=fara_dir, use_cache=True)
    assert isinstance(f2, Hit) and f2.coverage.cache_hits == 1

    f_abs1 = fara.search("MissingEntityXYZ", store=s_f, limiter=Limiter(s_f), cache_dir=fara_dir, use_cache=True)
    assert isinstance(f_abs1, VerifiedAbsence) and f_abs1.probes[0].endpoint == fara.FARA_PRINCIPALS_URL
    f_abs2 = fara.search("MissingEntityXYZ", store=s_f, limiter=Limiter(s_f), cache_dir=fara_dir, use_cache=True)
    assert isinstance(f_abs2, VerifiedAbsence) and f_abs2.coverage.cache_hits == 1

    # ICIJ
    ic_dir = tmp_path / "icij_c"
    nodes, rels = icij.parse_icij_payload(json.dumps({
        "nodes": [{"node_id": "10", "node_type": "entity", "name": "ORION HOLDINGS", "jurisdiction": "Panama"}],
        "relationships": [],
    }))
    icij.index_icij_data(icij.conn_for(ic_dir), nodes, rels)
    s_i = Store(tmp_path / "i.db")
    i1 = icij.search("ORION", store=s_i, limiter=Limiter(s_i), cache_dir=ic_dir, use_cache=True)
    assert isinstance(i1, Hit) and i1.probes[0].endpoint == icij.ICIJ_BULK_URL and i1.probes[0].result_count == 1
    i2 = icij.search("ORION", store=s_i, limiter=Limiter(s_i), cache_dir=ic_dir, use_cache=True)
    assert isinstance(i2, Hit) and i2.coverage.cache_hits == 1

    # Brokers
    br_dir = tmp_path / "brok_c"
    b_rows = brokers.parse_brokers_payload("Broker Name,State,Registration Number\nBeta Data LLC,CA,CA-1\n", default_state="CA")
    brokers.index_brokers(brokers.conn_for(br_dir), b_rows, state="CA")
    s_b = Store(tmp_path / "b.db")
    b1 = brokers.search("Beta Data", state="CA", store=s_b, limiter=Limiter(s_b), cache_dir=br_dir, use_cache=True)
    assert isinstance(b1, Hit) and b1.probes[0].endpoint == brokers.STATE_FEEDS["CA"] and b1.probes[0].at > 0
    b2 = brokers.search("Beta Data", state="CA", store=s_b, limiter=Limiter(s_b), cache_dir=br_dir, use_cache=True)
    assert isinstance(b2, Hit) and b2.coverage.cache_hits == 1


def test_spec_bounds_and_legacy_searxng_spec_alias():
    corpus, ns, cav = registry.spec_bounds("openalex")
    assert "OpenAlex" in corpus and ns and cav
    fallback = registry.spec_bounds("nonexistent_plugin_x", corpus="C", not_searched=("N",), caveats=("V",))
    assert fallback == ("C", ["N"], ["V"])

    alias = registry.get_spec("searxng")
    assert alias is not None and alias.name == "web"
