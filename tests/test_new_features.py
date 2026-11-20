"""Comprehensive tests for Waves 0-5:
  * Wave 0: field-feedback bug fixes (--version, propublica /appointees/,
    courtlistener --type rd, Hit.to_dict() asked/total_matches,
    BrowserToolMissing, gao.gov allow-list) and gate cancel (#14).
  * Wave 1: SourceSpec registry and `evidence-search sources` command.
  * Wave 2: USAspending follow-ons (--agency, --idv, --transactions,
    IDV PIID routing on search/sum, reporting-lag caveat).
  * Wave 3: SAM.gov client (#2) and CourtListener docket-watch mode (#18).
  * Wave 4: ProPublica Nonprofit / 990 client (#4) and SEC EDGAR client (#1).
  * Wave 5: Sanctions bulk client (#3) and optional CDP render backend (#26).
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time
import types

import pytest

from evidence_search import __version__, cli
from evidence_search.cli import (
    EXIT_ACCESS_BLOCKER,
    EXIT_HIT,
    EXIT_INTERNAL_ERROR,
    EXIT_NO_LOCAL_DATA,
    EXIT_USAGE,
    EXIT_VERIFIED_ABSENCE,
    main,
)
from evidence_search.core import browser as core_browser
from evidence_search.core import gates as core_gates
from evidence_search.core import http as core_http
from evidence_search.core.limits import Limiter
from evidence_search.core.registry import all_specs, get_spec
from evidence_search.core.results import (
    AccessBlocker,
    Blocker,
    Coverage,
    Hit,
    Probe,
    Result,
    VerifiedAbsence,
)
from evidence_search.core.store import Store


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


# ===========================================================================
# Wave 0: Bug fixes, allow-list, and gate cancel (#14)
# ===========================================================================

def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_gao_gov_is_on_browser_allow_list():
    ok, why = core_browser.host_allowed("https://www.gao.gov/products/gao-26-101")
    assert ok and "Accountability" in why


def test_propublica_disclosures_emits_plural_appointees_url(monkeypatch):
    """FEEDBACK.local.md 2026-10-08: /appointee/<slug> (singular) 404s; the
    working URL is /appointees/<slug> (plural)."""
    from evidence_search.sources import propublica_disclosures as pp

    payload = json.dumps({
        "nodes": [{
            "type": "data",
            "data": [
                {"q": 1, "result": 2},
                "cuthbert",
                [3],
                {"a_txt": 4, "a_slug": 5, "agency_name": 6, "title": 7},
                "Cuthbert West",
                "cuthbert-west",
                "Department of Commerce",
                "Advisor",
            ],
        }]
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (payload, None))
    s, L = _kit()
    out = pp.search("cuthbert", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.results[0].url.endswith("/appointees/cuthbert-west")


def test_courtlistener_type_rd_populates_title_and_snippet():
    """FEEDBACK.md 2026-09-20 Friction 3: --type rd rows lacked camelCase
    caseName/docketNumber/dateFiled and rendered blank titles and ' | | '."""
    from evidence_search.sources.courtlistener import _parse

    body = json.dumps({
        "count": 26,
        "results": [{
            "docket_id": 73489661,
            "case_name": "United States v. Sant",
            "docket_number": "0:26-cr-00123",
            "court_id": "mnd",
            "date_filed": "2026-09-16",
            "entry_number": 268,
            "description": "ORDER granting 266 Motion for Extension of Time to File",
            "absolute_url": "/docket/73489661/268/united-states-v-sant/",
            "filepath_local": "recap/gov.uscourts.mnd.234416/gov.uscourts.mnd.234416.268.0.pdf",
        }],
    })
    rows = _parse(body)
    assert len(rows) == 1
    r = rows[0]
    assert "United States v. Sant" in r.title
    assert "Doc. 268" in r.title
    assert "ORDER granting 266" in r.title
    assert r.snippet != " |  | "
    assert r.meta["pdf_url"] == (
        "https://storage.courtlistener.com/recap/gov.uscourts.mnd.234416/"
        "gov.uscourts.mnd.234416.268.0.pdf"
    )


def test_hit_to_dict_exposes_top_level_asked_and_total_matches():
    """FEEDBACK.md 2026-09-20 Friction 1 & 2: SKILL.md tells agents to read
    top-level `asked` in --json, and agents look for top-level `total_matches`."""
    hit = Hit(
        query='"Operation Keyhole"',
        coverage=Coverage(queried=["courtlistener"], responsive=["courtlistener"]),
        results=[Result(url="https://x", title="Case", source="courtlistener",
                        meta={"total_matches": 16})],
        probes=[Probe(source="courtlistener", query='"Operation Keyhole"',
                      params={"kind": "r"}, exact_match_supported=True)],
    )
    d = hit.to_dict()
    assert d["total_matches"] == 16
    assert d["asked"] == [{
        "query": '"Operation Keyhole"',
        "source": "courtlistener",
        "params": {"kind": "r"},
        "exact_match_supported": True,
    }]


def test_missing_playwright_binary_raises_tool_error_not_access_blocker(monkeypatch):
    """FEEDBACK.local.md 2026-10-08: a missing local Chromium binary was reported
    as AccessBlocker (http-5xx, exit 12) instead of a local tool failure (exit 70)."""

    class _FakePW:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        @property
        def chromium(self):
            class _C:
                def launch(self, **kw):
                    raise RuntimeError("BrowserType.launch: Executable doesn't exist at /path/chromium")
            return _C()

    fake_sync = types.SimpleNamespace(
        sync_playwright=lambda: _FakePW(),
        TimeoutError=TimeoutError,
    )
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=fake_sync))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    with pytest.raises(core_browser.BrowserToolMissing):
        core_browser.fetch("https://www.oscn.net/dockets/x", source="oscn")

    monkeypatch.setattr("sys.argv", ["evidence-search", "browser", "https://www.oscn.net/dockets/x"])
    assert cli.cli() == EXIT_INTERNAL_ERROR


def test_gate_cancel_by_token_and_bulk_filters(monkeypatch, capsys):
    """Issue #14: dismiss stale or test-run human gates by token or bulk filter."""
    s = Store()
    monkeypatch.setenv("EVIDENCE_TASK", "test-task-14")
    g1 = core_gates.open_gate(s, source="oscn", url="https://www.oscn.net/a", query="q1")
    g2 = core_gates.open_gate(s, source="oscn", url="https://www.oscn.net/b", query="q2")
    g3 = core_gates.open_gate(s, source="extract", url="https://www.courtlistener.com/c", query="q3")

    assert s.get_job(g1.resume_token)["payload"]["requested_by"] == "test-task-14"
    assert len(core_gates.list_gates(s)) == 3

    # Cancel single gate by token via CLI
    assert main(["gate", "cancel", g1.resume_token, "--reason", "leftover fixture"]) == 0
    capsys.readouterr()
    assert len(core_gates.list_gates(s)) == 2
    j1 = s.get_job(g1.resume_token)
    assert j1["state"] == "cancelled"
    assert j1["result"]["reason"] == "leftover fixture"

    # Age back g2 to 40 days ago and bulk-cancel oscn gates older than 30d
    s.conn.execute("UPDATE jobs SET created_at=? WHERE token=?",
                   (time.time() - 40 * 86400, g2.resume_token))
    s.conn.commit()
    assert main(["--json", "gate", "cancel", "--older-than", "30d", "--source", "oscn"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cancelled"] == [g2.resume_token]

    # Only g3 (extract, fresh) remains open
    remaining = core_gates.list_gates(s)
    assert [r["token"] for r in remaining] == [g3.resume_token]

    # Bulk cancel with no matches exits EXIT_NO_LOCAL_DATA (3)
    assert main(["gate", "cancel", "--older-than", "90d"]) == EXIT_NO_LOCAL_DATA


# ===========================================================================
# Wave 1: SourceSpec registry and `evidence-search sources`
# ===========================================================================

def test_sources_command_exposes_all_epistemic_manifests(capsys):
    specs = all_specs()
    names = {s.name for s in specs}
    assert {"news", "web", "oscn", "courtlistener", "propublica", "usaspending",
            "crossref", "fedreg", "docs", "lda", "fec", "samgov", "nonprofit",
            "sec", "sanctions"} <= names

    for s in specs:
        assert s.corpus and s.not_searched and s.caveats
        assert get_spec(s.name) == s
        assert get_spec(s.policy_name) == s

    assert main(["--json", "sources"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert len(catalog) == len(specs)

    assert main(["--json", "sources", "sec"]) == 0
    sec_manifest = json.loads(capsys.readouterr().out)
    assert sec_manifest["name"] == "sec"
    assert sec_manifest["counts"] is True

    assert main(["sources", "no-such-source"]) == EXIT_USAGE


# ===========================================================================
# Wave 2: USAspending follow-ons (--agency, --idv, --transactions, routing)
# ===========================================================================

def test_usaspending_agency_filter_and_alias_resolution(monkeypatch):
    from evidence_search.sources import usaspending as usa

    captured = {}

    def _spy(path, body, timeout=45.0):
        captured["filters"] = body["filters"]
        return {"results": {"contracts": 4}}, None

    monkeypatch.setattr(usa, "_post", _spy)
    s, L = _kit()
    out = usa.counts("Acme", agency="ICE", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert captured["filters"]["agencies"] == [{
        "type": "awarding",
        "tier": "subtier",
        "name": "U.S. Immigration and Customs Enforcement",
    }]


def test_usaspending_idv_piid_routes_to_idvs_on_search_and_sum(monkeypatch):
    from evidence_search.sources import usaspending as usa

    seen_types = []

    def _spy(path, body, timeout=45.0):
        seen_types.append(body["filters"]["award_type_codes"])
        return {"results": [{"Award ID": "70CMSW26D00000016", "Recipient Name": "ACME",
                             "Award Amount": 500.0}],
                "page_metadata": {"hasNext": False}}, None

    monkeypatch.setattr(usa, "_post", _spy)
    s, L = _kit()
    out = usa.search("70CMSW26D00000016", by_recipient=False, store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert seen_types[-1] == usa.IDV_TYPES

    s2, L2 = _kit()
    out2 = usa.dollar_sum("70CMSW26D00000016", by_recipient=False, store=s2, limiter=L2)
    assert isinstance(out2, Hit)
    assert seen_types[-1] == usa.IDV_TYPES


def test_usaspending_reporting_lag_caveat_on_recent_dates(monkeypatch):
    import datetime as _dt
    from evidence_search.sources import usaspending as usa

    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": {"contracts": 0}}, None))
    s, L = _kit()
    recent = _dt.date.today().isoformat()
    out = usa.counts("Acme", date_from=recent, store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert any("REPORTING LAG" in c for c in out.caveats)


def test_usaspending_idv_orders_and_transactions(monkeypatch):
    from evidence_search.sources import usaspending as usa

    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": [{"piid": "70CMSW26F00000001", "recipient_name": "ACME LLC",
                      "obligated_amount": 1250000.0, "awarding_agency": "DHS",
                      "description": "TASK ORDER 1"}],
         "page_metadata": {"hasNext": False}}, None))
    s, L = _kit()
    out = usa.idv_orders("70CMSW26D00000016", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.results[0].meta["piid"] == "70CMSW26F00000001"
    assert out.results[0].meta["obligated_amount"] == 1250000.0

    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": [{"Transaction ID": "tx1", "Award ID": "70CDCR26FR0000001",
                      "Modification Number": "P00003", "Action Date": "2026-09-25",
                      "Transaction Amount": 29900000.0,
                      "Transaction Description": "IGF::OT::IGF EXERCISE OPTION YEAR 2",
                      "Recipient Name": "VENDOR H INC"}],
         "page_metadata": {"hasNext": False}}, None))
    s2, L2 = _kit()
    tx_out = usa.transactions("70CDCR26FR0000001", store=s2, limiter=L2, use_cache=False)
    assert isinstance(tx_out, Hit)
    assert tx_out.results[0].meta["modification_number"] == "P00003"
    assert tx_out.results[0].meta["transaction_amount"] == 29900000.0


# ===========================================================================
# Wave 3: SAM.gov (#2) and CourtListener docket-watch (#18)
# ===========================================================================

def test_samgov_search_detail_and_attachments(monkeypatch):
    from evidence_search.sources import samgov

    search_payload = json.dumps({
        "_embedded": {
            "results": [{
                "_id": "opp123",
                "solicitationNumber": "70B03C26Q00000152",
                "title": "Realistic De-Escalation Instructor Course",
                "type": {"value": "Solicitation"},
                "organizationHierarchy": [{"name": "HOMELAND SECURITY, DEPARTMENT OF"},
                                          {"name": "US CUSTOMS AND BORDER PROTECTION"}],
                "publishDate": "2026-08-10",
                "descriptions": [{"content": "<p>Training at Laredo Sector station.</p>"}],
            }]
        },
        "page": {"totalElements": 1},
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (search_payload, None))
    s, L = _kit()
    out = samgov.search('"70B03C26Q00000152"', store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.results[0].meta["solicitation_number"] == "70B03C26Q00000152"
    assert "Laredo Sector" in out.results[0].meta["description"]

    detail_payload = json.dumps({
        "opportunityId": "opp123",
        "solicitationNumber": "70B03C26Q00000152",
        "title": "Realistic De-Escalation Instructor Course",
        "classificationCode": "U013",
        "naicsCode": "611519",
        "description": [{"body": "<p>Full statement of work for CBP station.</p>"}],
    })
    monkeypatch.setattr(samgov, "fetch", lambda *a, **k: (detail_payload, None))
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (detail_payload, None))
    s2, L2 = _kit()
    det = samgov.detail("opp123", store=s2, limiter=L2, use_cache=False)
    assert isinstance(det, Hit)
    assert det.results[0].meta["psc"] == "U013"
    assert "Full statement of work" in det.results[0].meta["description"]

    att_payload = json.dumps({
        "_embedded": {
            "opportunityAttachmentList": [{
                "attachments": [{
                    "resourceId": "res999",
                    "name": "Attachment_1_Historical_Location.pdf",
                    "accessLevel": "public",
                    "size": 45120,
                }]
            }]
        }
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (att_payload, None))
    s3, L3 = _kit()
    att = samgov.attachments("opp123", store=s3, limiter=L3, use_cache=False)
    assert isinstance(att, Hit)
    assert att.results[0].meta["resource_id"] == "res999"


def test_courtlistener_docket_watch_mode(monkeypatch):
    """Issue #18: requires `<court>:<number>`, extracts Last Updated, deadlines,
    related cases, full entry text, and bounds any absence with Last Updated."""
    from evidence_search.sources import courtlistener as cl

    s, L = _kit()
    # Bare docket number without <court>: is refused
    bare = cl.docket("1:26-cv-01352", store=s, limiter=L, use_cache=False)
    assert isinstance(bare, AccessBlocker)
    assert bare.mechanism is Blocker.WRONG_ID_TYPE

    api_json = json.dumps({
        "count": 2,
        "results": [
            {
                "docket_id": 73218916,
                "caseName": "Common Cause v. Department of Justice",
                "docketNumber": "1:26-cv-01352",
                "docket_absolute_url": "/docket/73218916/common-cause-v-doj/",
                "recap_documents": [
                    {
                        "entry_number": 51,
                        "date_filed": "2026-09-16",
                        "description": "MINUTE ORDER (Text-Only): Answer due for ALL FEDERAL DEFENDANTS by 10/15/2026.",
                    }
                ],
            },
            {
                "docket_id": 73299999,
                "caseName": "League of Women Voters v. DOJ",
                "docketNumber": "1:26-cv-01400",
                "docket_absolute_url": "/docket/73299999/lwv-v-doj/",
            },
        ],
    })
    docket_html = """
    <html><body>
      <h1>Common Cause v. Department of Justice</h1>
      <p>Last Updated: Sept. 18, 2026</p>
      <p>Date of Last Known Filing: Sept. 16, 2026</p>
      <table>
        <tr id="entry-51">
          <td>51</td><td>2026-09-16</td>
          <td>MINUTE ORDER (Text-Only): Answer due for ALL FEDERAL DEFENDANTS by 10/15/2026.</td>
        </tr>
        <tr id="entry-52">
          <td>52</td><td>2026-09-16</td>
          <td>MEMORANDUM OPINION AND ORDER. <a href="https://storage.courtlistener.com/recap/gov.uscourts.dcd.291580/gov.uscourts.dcd.291580.52.0.pdf">Download PDF</a></td>
        </tr>
      </table>
    </body></html>
    """

    def _fake_fetch(url, **kw):
        if "/api/rest/v4/search/" in url:
            return api_json, None
        return docket_html, None

    monkeypatch.setattr(cl, "fetch", _fake_fetch)
    s2, L2 = _kit()
    out = cl.docket("dcd:1:26-cv-01352", store=s2, limiter=L2, use_cache=False)
    assert isinstance(out, Hit)
    assert len(out.results) == 2
    m = out.results[0].meta
    assert m["last_updated"] == "Sept. 18, 2026"
    assert m["date_last_filing"] == "Sept. 16, 2026"
    assert any("Answer due" in d for d in m["deadlines"])
    assert any("League of Women Voters" in rc for rc in m["related_cases"])
    assert out.results[0].meta["is_text_only"] is True
    assert out.results[1].meta["pdf_url"].endswith(".52.0.pdf")

    # Filtered grep with no match returns a VerifiedAbsence carrying the Last Updated timestamp
    s3, L3 = _kit()
    abs_out = cl.docket("dcd:1:26-cv-01352", grep_pattern="PRELIMINARY INJUNCTION",
                        store=s3, limiter=L3, use_cache=False)
    assert isinstance(abs_out, VerifiedAbsence)
    assert "Sept. 18, 2026" in abs_out.searched
    assert any("Sept. 18, 2026" in c for c in abs_out.caveats)


# ===========================================================================
# Wave 4: Nonprofit 990s (#4) and SEC EDGAR (#1)
# ===========================================================================

def test_nonprofit_name_collision_and_mismatch_warnings(monkeypatch):
    """Issue #4 & extension-candidates.md:
      * 'Donors Trust' returns 2 distinct EINs (Omaha NE vs Alexandria VA) -> must warn.
      * 'DonorsTrust' returns 'Donorsrus Inc' -> must warn that the name doesn't match.
    """
    from evidence_search.sources import nonprofit as np_mod

    two_eins = json.dumps({
        "total_results": 2,
        "organizations": [
            {"ein": 262515785, "strein": "26-2515785", "name": "Donors Trust",
             "city": "Omaha", "state": "NE", "ntee_code": "T20", "subseccd": 3},
            {"ein": 522166327, "strein": "52-2166327", "name": "Donors Trust Inc",
             "city": "Alexandria", "state": "VA", "ntee_code": "T31", "subseccd": 3},
        ],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (two_eins, None))
    s, L = _kit()
    out = np_mod.search("Donors Trust", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    cav = out.results[0].meta["entity_caveat"]
    assert cav and "2 DISTINCT EINs" in cav
    assert "26-2515785" in cav and "52-2166327" in cav

    mismatch = json.dumps({
        "total_results": 1,
        "organizations": [
            {"ein": 123456789, "strein": "12-3456789", "name": "Donorsrus Inc",
             "city": "Jupiter", "state": "FL"},
        ],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (mismatch, None))
    s2, L2 = _kit()
    out2 = np_mod.search("DonorsTrust", store=s2, limiter=L2, use_cache=False)
    assert isinstance(out2, Hit)
    assert "does not contain your query string" in out2.results[0].meta["entity_caveat"]


def test_nonprofit_by_ein_and_year_filter(monkeypatch):
    from evidence_search.sources import nonprofit as np_mod

    org_json = json.dumps({
        "organization": {
            "ein": 522166327, "name": "DONORS TRUST INC",
            "city": "ALEXANDRIA", "state": "VA", "ntee_code": "T31",
        },
        "filings_with_data": [
            {"tax_prd_yr": 2024, "formtype": 0, "totrevenue": 145000000,
             "totfuncexpns": 130000000, "totassetsend": 410000000,
             "pdf_url": "https://projects.propublica.org/nonprofits/download-filing?path=2024.pdf"},
        ],
        "filings_without_data": [],
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (org_json, None))
    s, L = _kit()
    out = np_mod.by_ein("52-2166327", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.results[0].meta["tax_year"] == 2024
    assert out.results[0].meta["total_revenue"] == 145000000

    s2, L2 = _kit()
    abs_out = np_mod.by_ein("52-2166327", year=1998, store=s2, limiter=L2, use_cache=False)
    assert isinstance(abs_out, VerifiedAbsence)


def test_sec_edgar_fulltext_and_company_submissions(monkeypatch):
    """Issue #1: EFTS full-text search (quoted exact phrase) and CIK submissions."""
    from evidence_search.sources import sec

    efts_payload = json.dumps({
        "hits": {
            "total": {"value": 1},
            "hits": [{
                "_id": "0001070985-26-000012:cxw-20260630.htm",
                "_source": {
                    "ciks": ["0001070985"],
                    "display_names": ["CoreCivic, Inc. (CXW) (CIK 0001070985)"],
                    "form": "10-Q",
                    "file_date": "2026-08-05",
                    "period_ending": "2026-06-30",
                    "adsh": "0001070985-26-000012",
                },
            }],
        }
    })
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (efts_payload, None))
    s, L = _kit()
    out = sec.fulltext('"CoreCivic"', forms="10-Q", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.probes[0].exact_match_supported is True
    assert out.results[0].meta["cik"] == "0001070985"
    assert out.results[0].meta["form"] == "10-Q"
    assert "Archives/edgar/data/1070985/000107098526000012/cxw-20260630.htm" in out.results[0].url

    tickers_json = json.dumps({
        "0": {"cik_str": 1070985, "ticker": "CXW", "title": "CORECIVIC, INC."},
        "1": {"cik_str": 923796, "ticker": "GEO", "title": "GEO GROUP INC"},
    })
    sub_json = json.dumps({
        "cik": "1070985",
        "name": "CORECIVIC, INC.",
        "tickers": ["CXW"],
        "sic": "8744",
        "sicDescription": "FACILITIES SUPPORT MANAGEMENT SERVICES",
        "filings": {
            "recent": {
                "accessionNumber": ["0001070985-26-000012", "0001070985-26-000008"],
                "form": ["10-Q", "8-K"],
                "filingDate": ["2026-08-05", "2026-07-15"],
                "reportDate": ["2026-06-30", "2026-07-15"],
                "primaryDocument": ["cxw-20260630.htm", "cxw-8k.htm"],
                "primaryDocDescription": ["10-Q", "8-K"],
            }
        },
    })

    def _sec_fetch(url, **kw):
        if "company_tickers.json" in url:
            return tickers_json, None
        return sub_json, None

    monkeypatch.setattr(sec, "fetch", _sec_fetch)
    monkeypatch.setattr(core_http, "fetch", _sec_fetch)
    s2, L2 = _kit()
    sub_out = sec.submissions("CXW", forms="8-K", store=s2, limiter=L2, use_cache=False)
    assert isinstance(sub_out, Hit)
    assert len(sub_out.results) == 1
    assert sub_out.results[0].meta["form"] == "8-K"


# ===========================================================================
# Wave 5: Sanctions (#3) and Optional CDP Render Backend (#26)
# ===========================================================================

OFAC_FIXTURE = (
    '28101,"INTELLEXA S.A.",-0-,"CYBER2",-0-,-0-,-0-,-0-,-0-,-0-,-0-,'
    '"a.k.a. \'INTELLEXA LIMITED\'; Registration ID 12345."\n'
    '28102,"DILIAN, Tal",-0-,"CYBER2","Founder",-0-,-0-,-0-,-0-,-0-,-0-,'
    '"DOB 15 May 1968; POB Jerusalem; a.k.a. \'Tal Jonathan Dilian\'."\n'
)

EU_FIXTURE = (
    "Entity_LogicalId;NameAlias_WholeName;Entity_Regulation_Programme;"
    "BirthDate_Year;Address_CountryIso2Code;Entity_Regulation_PublicationUrl;"
    "Entity_SubjectType_ClassificationCode\n"
    "9001;Aleksandr Grigoryevich LUKASHENKO;BLR;1954;BY;https://eur-lex.europa.eu/eli/reg/2006/765;person\n"
    "9001;Alyaksandr Hryhoryavich LUKASHENKA;BLR;1954;BY;https://eur-lex.europa.eu/eli/reg/2006/765;person\n"
    "9001;Alexander LUKASHENKO;BLR;1954;BY;https://eur-lex.europa.eu/eli/reg/2006/765;person\n"
)


def test_sanctions_alias_grouping_and_exact_vs_possible_matching(tmp_path):
    """Issue #3: EU rows grouped by Entity_LogicalId, OFAC a.k.a. remarks parsed,
    exact matches vs possible_match (Jaro-Winkler >= 0.85), and dirty coverage
    refusing to certify absence if one list fails."""
    from evidence_search.sources import sanctions as sn

    s, L = _kit()
    cache_dir = tmp_path / "sanctions_cache"
    conn = sn.conn_for(cache_dir)
    ofac_ents, ofac_aliases = sn.parse_ofac_csv(OFAC_FIXTURE)
    eu_ents, eu_aliases = sn.parse_eu_csv(EU_FIXTURE)
    sn.index_sanctions_data(conn, "ofac", ofac_ents, ofac_aliases)
    sn.index_sanctions_data(conn, "eu", eu_ents, eu_aliases)

    # 1. Exact match via OFAC a.k.a. alias (with legal suffix "LIMITED" stripped)
    out1 = sn.search("Intellexa LLC", store=s, limiter=L, cache_dir=cache_dir, use_cache=False)
    assert isinstance(out1, Hit)
    assert out1.results[0].meta["match_quality"] == "exact_match"
    assert out1.results[0].meta["primary_name"] == "INTELLEXA S.A."
    assert out1.results[0].meta["verify_note"] is None

    # 2. EU LogicalId grouping: 3 CSV rows for LogicalId 9001 collapse to 1 entity with 3 aliases
    out2 = sn.search("Alyaksandr Lukashenka", store=s, limiter=L, cache_dir=cache_dir, use_cache=False)
    assert isinstance(out2, Hit)
    assert len(out2.results) == 1
    assert out2.results[0].meta["entity_id"] == "9001"
    assert out2.results[0].meta["alias_count"] == 3
    assert out2.results[0].meta["birth_year"] == "1954"
    assert out2.results[0].meta["programme"] == "BLR"

    # 3. Transliteration fuzzy match (<1.0 score -> possible_match, never exact_match)
    out3 = sn.search("Aleksander Lukashenka", store=s, limiter=L, cache_dir=cache_dir, use_cache=False)
    assert isinstance(out3, Hit)
    assert out3.results[0].meta["match_quality"] == "possible_match"
    assert out3.results[0].meta["verify_note"] is not None

    # 4. --exact suppresses fuzzy possible_match and returns a scoped VerifiedAbsence
    out4 = sn.search("Aleksander Lukashenka", exact_only=True,
                     store=s, limiter=L, cache_dir=cache_dir, use_cache=False)
    assert isinstance(out4, VerifiedAbsence)
    assert any("EXACT-MATCH ONLY" in c for c in out4.caveats)


def test_cdp_render_backend_for_ungated_js_pages(monkeypatch, capsys):
    """Issue #26: when EVIDENCE_BROWSER_CDP is set and extract hits an ungated
    JS_ONLY SPA shell, it renders through connect_over_cdp and discloses the
    render backend in the output."""
    connected_urls = []

    class _FakePage:
        def goto(self, url, **kw):
            pass
        def wait_for_selector(self, *a, **kw):
            pass
        def wait_for_timeout(self, ms):
            pass
        def content(self):
            return ("<html><body><main><h1>Contract 70CDCR26FR0000001</h1><p>"
                    + "Rendered contract scope details from JS SPA. " * 25
                    + "</p></main></body></html>")
        def title(self):
            return "HigherGov Contract Detail"

    class _FakeBrowser:
        def new_context(self, **kw):
            class _Ctx:
                def new_page(self):
                    return _FakePage()
            return _Ctx()
        def close(self):
            pass

    class _FakePW:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        @property
        def chromium(self):
            class _C:
                def connect_over_cdp(self, url):
                    connected_urls.append(url)
                    return _FakeBrowser()
            return _C()

    fake_sync = types.SimpleNamespace(
        sync_playwright=lambda: _FakePW(),
        TimeoutError=TimeoutError,
    )
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=fake_sync))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    monkeypatch.setenv("EVIDENCE_BROWSER_CDP", "ws://127.0.0.1:9222/devtools/browser/obscura")

    monkeypatch.setattr(core_http, "fetch", lambda url, **kw: (
        None, AccessBlocker(query=url, mechanism=Blocker.JS_ONLY, url=url, detail="HTTP 200")))

    rc = main(["extract", "https://www.highergov.com/contract/70CDCR26FR0000001/",
               "--grep", "70CDCR26FR0000001"])
    out = capsys.readouterr().out
    assert rc == 0
    assert connected_urls == ["ws://127.0.0.1:9222/devtools/browser/obscura"]
    assert "rendered: cdp:ws://127.0.0.1:9222/devtools/browser/obscura" in out
    assert "70CDCR26FR0000001" in out

    # Also triggers when an SSR shell has >200 chars of nav chrome + <script>,
    # and --grep finds 0 matches in the unhydrated HTML.
    ssr_nav_shell = (
        "<html><head><script src='/app.js'></script></head><body><nav>"
        + "HigherGov Federal Contracts Opportunities Awards Agencies Search " * 6
        + "</nav><div id='__next'></div></body></html>"
    )
    monkeypatch.setattr(core_http, "fetch", lambda url, **kw: (ssr_nav_shell, None))
    rc2 = main(["extract", "https://www.highergov.com/contract/70CDCR26FR0000002/",
                "--grep", "70CDCR26FR0000001"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "rendered: cdp:ws://127.0.0.1:9222/devtools/browser/obscura" in out2
    assert "70CDCR26FR0000001" in out2

