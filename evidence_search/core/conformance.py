"""Plugin and source conformance harness (`evidence-search plugin check`).

Audits any registered source (built-in, shipped manifest, user JSON manifest,
or Python entry-point plugin) against `evidence-search`'s six contracts:

  1. `manifest_valid` (`validate_spec` epistemic manifest check)
  2. `transport_failure_safe` (network failures never yield VerifiedAbsence)
  3. `rate_limit_safe` (exhausted limiter budget always yields RateLimited)
  4. `malformed_payload_safe` (unexpected `{}` / soft-block HTML never yields VerifiedAbsence)
  5. `positive_fixture_ok` (known positive response yields Hit with >=1 result)
  6. `empty_fixture_ok` (known empty-envelope response yields VerifiedAbsence with scopes)

Plus optional `--live` mode (`check_source(..., live=True)`) to run a real
network canary against the upstream endpoint and catch schema drift.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
from typing import Callable
import zipfile

from . import http as core_http
from . import limits as core_limits
from .limits import Limiter
from .registry import SourceSpec, all_specs, get_spec, validate_spec
from .results import AccessBlocker, Blocker, Coverage, Hit, RateLimited, VerifiedAbsence
from .store import Store


_POS_HTTP_FIXTURES: dict[str, str] = {
    "news_rss.search": (
        '<rss><channel><title>Google News</title><item>'
        '<title>Probe Contract Reported</title>'
        '<link>https://news.google.com/rss/articles/CBMi1</link>'
        '<description>&lt;a href="https://example.org/1"&gt;Headline&lt;/a&gt; Example Times</description>'
        '</item></channel></rss>'
    ),
    "web.search": json.dumps([{
        "url": "https://example.gov/report",
        "title": [{"value": "Probe Report", "is_bold": False}],
        "extract": [{"value": "Contract summary", "is_bold": False}],
        "source": "mwmbl",
    }]),
    "searxng.search": json.dumps({
        "results": [{"url": "https://example.gov/report", "title": "Probe Report",
                     "content": "Contract summary", "engine": "google"}],
        "unresponsive_engines": [],
    }),
    "crossref.search": json.dumps({
        "message": {
            "total-results": 1,
            "items": [{
                "DOI": "10.1000/probe", "title": ["Probe Empirical Study"],
                "container-title": ["Journal of Empirical Research"],
                "issued": {"date-parts": [[2025]]},
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "URL": "https://doi.org/10.1000/probe",
            }],
        }
    }),
    "federal_register.search": json.dumps({
        "count": 1,
        "results": [{
            "document_number": "2026-01234",
            "title": "Probe Detention Rule",
            "type": "Rule",
            "publication_date": "2026-03-01",
            "html_url": "https://www.federalregister.gov/d/2026-01234",
            "agencies": [{"name": "Department of Homeland Security"}],
        }],
    }),
    "courtlistener.search": json.dumps({
        "count": 1,
        "results": [{
            "docket_id": 73218916,
            "caseName": "Probe v. United States",
            "docketNumber": "1:26-cv-01352",
            "court_id": "dcd",
            "absolute_url": "/docket/73218916/probe-v-us/",
        }],
    }),
    "courtlistener.docket": json.dumps({
        "count": 1,
        "results": [{
            "docket_id": 73218916,
            "caseName": "Common Cause v. Department of Justice",
            "docketNumber": "1:26-cv-01352",
            "docket_absolute_url": "/docket/73218916/common-cause-v-doj/",
            "recap_documents": [{"entry_number": 1, "date_filed": "2026-09-16", "description": "Complaint"}],
        }],
    }),
    "propublica_disclosures.search": json.dumps({
        "nodes": [{"type": "data", "data": [
            {"q": 1, "result": 2},
            "probe",
            [3],
            {"a_txt": 4, "a_slug": 5, "agency_name": 6, "title": 7},
            "Probe Official",
            "probe-official",
            "Department of Commerce",
            "Advisor",
        ]}]
    }),
    "oscn.search": (
        '<html><body><table><tr>'
        '<td><a href="GetCaseInformation.aspx?db=caddo&amp;number=CF-2025-00001">CF-2025-00001</a></td>'
        '<td>2025-01-15</td><td>Probe v. State</td>'
        '</tr></table></body></html>'
    ),
    "docs.search": "- [Probe Docs](https://code.claude.com/docs/en/probe)\n",
    "lda.search": json.dumps({
        "count": 1, "next": None, "previous": None,
        "results": [{
            "filing_uuid": "11111111-2222-3333-4444-555555555555",
            "filing_type": "Q1", "filing_year": 2025,
            "client": {"name": "Probe LLC"},
            "registrant": {"name": "K Street Advisors"},
            "expenses": "120000.00",
        }],
    }),
    "samgov.search": json.dumps({
        "_embedded": {"results": [{
            "_id": "opp-probe-1",
            "solicitationNumber": "70B03C26Q00000152",
            "title": "Probe Detention Support",
            "publishDate": "2026-04-01",
        }]},
        "page": {"totalElements": 1},
    }),
    "nonprofit.search": json.dumps({
        "total_results": 1,
        "organizations": [{
            "ein": 522166327, "strein": "52-2166327",
            "name": "Probe Foundation", "city": "Alexandria", "state": "VA",
        }],
    }),
    "sec.fulltext": json.dumps({
        "hits": {"total": {"value": 1}, "hits": [{
            "_id": "0001070985-26-000012:probe.htm",
            "_source": {
                "ciks": ["0001070985"], "display_names": ["Probe Corp"],
                "form": "10-Q", "file_date": "2026-08-05", "adsh": "0001070985-26-000012",
            },
        }]}
    }),
    "sec.submissions": json.dumps({
        "cik": "0001070985", "name": "Probe Corp", "tickers": ["PRB"],
        "filings": {"recent": {
            "accessionNumber": ["0001070985-26-000012"],
            "form": ["10-Q"], "filingDate": ["2026-08-05"],
            "reportDate": ["2026-06-30"], "primaryDocument": ["probe.htm"],
        }},
    }),
    "wayback.search": json.dumps([
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["20260312140000", "https://example.gov/contract", "text/html", "200", "SHA1AAA111", "4200"],
    ]),
    "openalex.search": json.dumps({
        "meta": {"count": 1},
        "results": [{
            "id": "https://openalex.org/W123456",
            "doi": "https://doi.org/10.1000/probe",
            "title": "Probe Research Paper",
            "publication_year": 2025,
            "cited_by_count": 5,
            "is_retracted": False,
        }],
    }),
    "socrata.query": json.dumps([
        {"vendor_name": "Probe Services LLC", "agency_name": "DOC", "amount": "500000"},
    ]),
    "arcgis.query": json.dumps({
        "features": [{"attributes": {"OwnerName": "Probe Holdings LLC", "CountyName": "Gallatin", "PARCELID": "01-23"}}],
    }),
    "kev.search": json.dumps({
        "title": "CISA KEV",
        "vulnerabilities": [{
            "cveID": "CVE-2025-9999",
            "vendorProject": "ProbeVendor",
            "product": "ProbeGateway",
            "vulnerabilityName": "Probe Remote Code Execution",
            "dateAdded": "2025-02-01",
            "dueDate": "2025-02-22",
            "knownRansomwareCampaignUse": "Known",
            "requiredAction": "Apply vendor patch",
        }],
    }),
    "crtsh.search": json.dumps([{
        "id": 998877, "common_name": "probe.example.gov",
        "name_value": "probe.example.gov\napi.probe.example.gov",
        "issuer_name": "Let's Encrypt", "not_before": "2026-01-01T00:00:00",
        "not_after": "2026-04-01T00:00:00",
    }]),
    "epa.search": json.dumps({
        "Results": {
            "QueryRows": "1",
            "Facilities": [{
                "RegistryID": "110000000001", "FacName": "Probe Industrial Facility",
                "FacStreet": "100 Main St", "FacCity": "Baton Rouge", "FacState": "LA",
                "FacZip": "70801", "FacQtrsWithNC": "4", "FacFormalActionCount": "1",
                "FacTotalPenalties": "$25,000",
            }],
        }
    }),
    "openfda.search": json.dumps({
        "meta": {"results": {"total": 1}},
        "results": [{
            "recall_number": "Z-1234-2026", "recalling_firm": "Probe Medical Corp",
            "product_description": "Infusion Pump", "reason_for_recall": "Probe battery fault",
            "event_date_initiated": "20260115",
        }],
    }),
    "cfpb.search": json.dumps({
        "hits": {
            "total": {"value": 1},
            "hits": [{
                "_id": "998811",
                "_source": {
                    "complaint_id": "998811", "company": "Probe Financial LLC",
                    "product": "Debt collection", "issue": "False statements",
                    "date_received": "2026-02-10", "state": "TX",
                    "company_response": "Closed with explanation",
                },
            }],
        }
    }),
    "gdelt.search": json.dumps({
        "articles": [{
            "url": "https://news.example.org/probe", "title": "Probe News Report",
            "domain": "news.example.org", "seendate": "20260301T120000Z",
            "sourcecountry": "United States", "language": "English",
        }],
    }),
    "rest.query": json.dumps({
        "count": 1,
        "results": [{"title": "Probe Dataset", "name": "probe-dataset", "notes": "Probe summary"}],
        "result": {
            "count": 1,
            "results": [{"title": "Probe Dataset", "name": "probe-dataset", "notes": "Probe summary"}],
        },
    }),
}

_EMPTY_HTTP_FIXTURES: dict[str, str] = {
    "news_rss.search": "<rss><channel><title>x</title></channel></rss>",
    "web.search": '[]',
    "searxng.search": '{"results": [], "unresponsive_engines": []}',
    "crossref.search": '{"message": {"total-results": 0, "items": []}}',
    "federal_register.search": '{"count": 0, "results": []}',
    "courtlistener.search": '{"count": 0, "results": []}',
    "courtlistener.docket": '{"count": 0, "results": []}',
    "propublica_disclosures.search": '{"nodes": [{"type": "data", "data": [{"q": 1, "result": 2}, "probe", []]}]}',
    "oscn.search": "<html><body>Found No Records</body></html>",
    "docs.search": "- [Hooks](https://code.claude.com/docs/hooks)\n",
    "lda.search": '{"count": 0, "next": null, "previous": null, "results": []}',
    "samgov.search": '{"_embedded": {"results": []}, "page": {"totalElements": 0}}',
    "nonprofit.search": '{"total_results": 0, "organizations": []}',
    "sec.fulltext": '{"hits": {"total": {"value": 0}, "hits": []}}',
    "sec.submissions": '{"name": "ACME CORP", "filings": {"recent": {"accessionNumber": []}}}',
    "wayback.search": '[["timestamp","original","mimetype","statuscode","digest","length"]]',
    "openalex.search": '{"meta": {"count": 0}, "results": []}',
    "socrata.query": '[{"count": "0"}]',
    "arcgis.query": '{"features": []}',
    "kev.search": '{"title": "CISA KEV", "vulnerabilities": []}',
    "crtsh.search": '[]',
    "epa.search": '{"Results": {"QueryRows": "0", "Facilities": []}}',
    "openfda.search": '{"error": {"code": "NOT_FOUND", "message": "No matches found!"}}',
    "cfpb.search": '{"hits": {"total": {"value": 0}, "hits": []}}',
    "gdelt.search": '{"articles": []}',
    "rest.query": '{"count": 0, "results": [], "result": {"count": 0, "results": []}}',
}


def _entry_points_for(spec: SourceSpec, tmp_dir: Path) -> list[tuple[str, str, Callable]]:
    """Return `(label, policy_name, fn(store, limiter))` callables for `spec`."""
    name = spec.name
    pol = spec.policy_name

    # Declarative manifest or custom callable plugin
    if spec.run is not None:
        return [(
            f"{name}.run",
            pol,
            lambda s, L: spec.run("probe", store=s, limiter=L, use_cache=False),
        )]

    if name == "news":
        from ..engines import news_rss
        return [("news_rss.search", pol,
                 lambda s, L: news_rss.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "web":
        from ..engines import web
        return [("web.search", pol,
                 lambda s, L: web.search("probe", provider="mwmbl",
                                         store=s, limiter=L, use_cache=False))]
    if name == "oscn":
        from ..sources import oscn
        return [("oscn.search", pol,
                 lambda s, L: oscn.search("caddo", lname="Probe", store=s, limiter=L, use_cache=False))]
    if name == "courtlistener":
        from ..sources import courtlistener
        return [
            ("courtlistener.search", pol,
             lambda s, L: courtlistener.search("probe", store=s, limiter=L, use_cache=False)),
            ("courtlistener.docket", pol,
             lambda s, L: courtlistener.docket("dcd:1:26-cv-01352", store=s, limiter=L, use_cache=False)),
        ]
    if name == "propublica":
        from ..sources import propublica_disclosures
        return [("propublica_disclosures.search", pol,
                 lambda s, L: propublica_disclosures.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "usaspending":
        from ..sources import usaspending
        return [
            ("usaspending.search", pol,
             lambda s, L: usaspending.search("Probe LLC", store=s, limiter=L, use_cache=False)),
            ("usaspending.counts", pol,
             lambda s, L: usaspending.counts("Probe LLC", store=s, limiter=L, use_cache=False)),
            ("usaspending.idv_orders", pol,
             lambda s, L: usaspending.idv_orders("70CDCR20D00000011", store=s, limiter=L, use_cache=False)),
            ("usaspending.transactions", pol,
             lambda s, L: usaspending.transactions("CONT_AWD_0001", store=s, limiter=L, use_cache=False)),
        ]
    if name == "crossref":
        from ..sources import crossref
        return [("crossref.search", pol,
                 lambda s, L: crossref.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "fedreg":
        from ..sources import federal_register
        return [("federal_register.search", pol,
                 lambda s, L: federal_register.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "docs":
        from ..sources import docs
        return [("docs.search", pol,
                 lambda s, L: docs.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "lda":
        from ..sources import lda
        return [("lda.search", pol,
                 lambda s, L: lda.search(client_name="Probe LLC", store=s, limiter=L, use_cache=False))]
    if name == "fec":
        from ..sources import fec
        return [("fec.between", pol,
                 lambda s, L: fec.between("C00000001", "C00000002", 2008, 2008,
                                          store=s, limiter=L, cache_dir=tmp_dir / "fec", use_cache=False))]
    if name == "samgov":
        from ..sources import samgov
        return [("samgov.search", pol,
                 lambda s, L: samgov.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "nonprofit":
        from ..sources import nonprofit
        return [("nonprofit.search", pol,
                 lambda s, L: nonprofit.search("Probe Foundation", store=s, limiter=L, use_cache=False))]
    if name == "sec":
        from ..sources import sec
        return [
            ("sec.fulltext", pol,
             lambda s, L: sec.fulltext('"Probe Corp"', store=s, limiter=L, use_cache=False)),
            ("sec.submissions", pol,
             lambda s, L: sec.submissions("0001070985", store=s, limiter=L, use_cache=False)),
        ]
    if name == "sanctions":
        from ..sources import sanctions
        return [("sanctions.search", pol,
                 lambda s, L: sanctions.search("Probe Entity", store=s, limiter=L,
                                               cache_dir=tmp_dir / "sanctions", use_cache=False))]
    if name == "fara":
        from ..sources import fara
        return [("fara.search", pol,
                 lambda s, L: fara.search("Probe Entity", store=s, limiter=L,
                                          cache_dir=tmp_dir / "fara", use_cache=False))]
    if name == "wayback":
        from ..sources import wayback
        return [("wayback.search", pol,
                 lambda s, L: wayback.search("https://example.gov/contract", store=s, limiter=L, use_cache=False))]
    if name == "openalex":
        from ..sources import openalex
        return [("openalex.search", pol,
                 lambda s, L: openalex.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "socrata":
        from ..sources import socrata
        return [("socrata.query", pol,
                 lambda s, L: socrata.query("data.cityofnewyork.us", "abcd-1234", "probe",
                                            store=s, limiter=L, use_cache=False))]
    if name == "arcgis":
        from ..sources import arcgis
        return [("arcgis.query", pol,
                 lambda s, L: arcgis.query("https://gis.example.gov/arcgis/rest/services/Parcels/FeatureServer/0",
                                           "probe", field="OWNER", store=s, limiter=L, use_cache=False))]
    if name == "kev":
        from ..sources import kev
        return [("kev.search", pol,
                 lambda s, L: kev.search("probe", store=s, limiter=L, use_cache=False))]
    if name == "crtsh":
        from ..sources import crtsh
        return [("crtsh.search", pol,
                 lambda s, L: crtsh.search("probe.example.gov", store=s, limiter=L, use_cache=False))]
    if name == "epa":
        from ..sources import epa
        return [("epa.search", pol,
                 lambda s, L: epa.search("Probe", store=s, limiter=L, use_cache=False))]
    if name == "openfda":
        from ..sources import openfda
        return [("openfda.search", pol,
                 lambda s, L: openfda.search("Probe", store=s, limiter=L, use_cache=False))]
    if name == "cfpb":
        from ..sources import cfpb
        return [("cfpb.search", pol,
                 lambda s, L: cfpb.search("Probe", store=s, limiter=L, use_cache=False))]
    if name == "icij":
        from ..sources import icij
        return [("icij.search", pol,
                 lambda s, L: icij.search("Probe Entity", store=s, limiter=L,
                                          cache_dir=tmp_dir / "icij", use_cache=False))]
    if name == "brokers":
        from ..sources import brokers
        return [("brokers.search", pol,
                 lambda s, L: brokers.search("Probe Data", state="CA", store=s, limiter=L,
                                             cache_dir=tmp_dir / "brokers", use_cache=False))]
    if name == "gdelt":
        from ..sources import gdelt
        return [("gdelt.search", pol,
                 lambda s, L: gdelt.search("probe", store=s, limiter=L, use_cache=False))]
    return []


def iter_fetch_modules() -> list[object]:
    """Return every `evidence_search.*` module that exposes a module-level `fetch`."""
    import sys
    from ..sources import courtlistener as cl_src
    from ..sources import samgov as sg_src
    from ..sources import sec as sec_src
    from ..sources import fara as fara_src
    from ..sources import icij as icij_src
    from ..sources import brokers as brok_src
    from ..sources import socrata as soc_src
    from ..sources import wayback as wb_src
    from ..sources import crtsh as crt_src
    from ..sources import epa as epa_src
    from ..engines import searxng as sx_src
    from ..engines import web as web_src

    targets: list[object] = [
        core_http, cl_src, sg_src, sec_src, sx_src, web_src,
        fara_src, icij_src, brok_src, soc_src, wb_src, crt_src, epa_src,
    ]
    for mod_name, mod in list(sys.modules.items()):
        if mod is not None and mod_name.startswith("evidence_search.") and hasattr(mod, "fetch"):
            if mod not in targets:
                targets.append(mod)
    return targets


def patch_fetch_targets(fn_replace: Callable) -> list[tuple[object, str, object]]:
    """Patch `fetch` across every loaded module and return `(mod, attr, orig)` restore tokens."""
    saved: list[tuple[object, str, object]] = []
    for mod in iter_fetch_modules():
        if hasattr(mod, "fetch"):
            saved.append((mod, "fetch", getattr(mod, "fetch")))
            setattr(mod, "fetch", fn_replace)
    return saved


def _patch_fetches(fn_replace: Callable, *, mode: str, tmp_root: Path,
                   sub_dir: str = "sub") -> list[tuple[object, str, object]]:
    """Temporarily replace `fetch` (and custom transport hooks) across loaded modules."""
    from ..sources import fec as fec_src
    from ..sources import sanctions as sn_src
    from ..sources import usaspending as us_src

    saved = patch_fetch_targets(fn_replace)

    saved.append((us_src, "_post", us_src._post))
    saved.append((fec_src, "_download", fec_src._download))
    saved.append((sn_src, "download_bulk_file", sn_src.download_bulk_file))

    work = tmp_root / sub_dir
    work.mkdir(parents=True, exist_ok=True)

    if mode == "dead":
        us_src._post = lambda p, pl: (None, ("http", "conformance dead network"))
        fec_src._download = lambda base, cycle, cache_dir, timeout=180.0: (
            None, AccessBlocker(query=f"{base}{cycle}", mechanism=Blocker.SERVER_ERROR,
                                url="u", detail="conformance dead network"))
        sn_src.download_bulk_file = lambda url, dest, **kw: (
            None, AccessBlocker(query="q", mechanism=Blocker.SERVER_ERROR,
                                url=url, detail="conformance dead network"))
    elif mode == "malformed":
        us_src._post = lambda p, pl: ({"unexpected": "shape"}, None)
        gz = work / "malformed_fec.zip"
        with zipfile.ZipFile(gz, "w") as zf:
            zf.writestr("itpas2.txt", "bad|row\n")
        fec_src._download = lambda base, cycle, cache_dir, timeout=180.0: (gz, None)
        gcsv = work / "malformed_sanctions.csv"
        gcsv.write_text("{}")
        sn_src.download_bulk_file = lambda url, dest, **kw: (gcsv, None)
    elif mode == "positive":
        us_src._post = lambda p, pl: (
            {"results": ({g: 1 if g == "contracts" else 0 for g in us_src.AWARD_GROUPS}
                         if "count" in p else
                         [{"Award ID": "CONT_AWD_0001", "Recipient Name": "Probe LLC",
                           "Award Amount": 150000.0, "Description": "Probe service",
                           "generated_internal_id": "CONT_AWD_0001"}]),
             "page_metadata": {"hasNext": False}},
            None,
        )
        pz = work / "pos_pas2.zip"
        oz = work / "pos_oth.zip"
        with zipfile.ZipFile(pz, "w") as zf:
            zf.writestr(
                "itpas2.txt",
                "C00000001|N|YE|P2008|28930167488|24K|CCM|PROBE RECIPIENT|WASHINGTON|DC|20001|||12032007|5000|C00000002|H0MI08042|SB23.4889|318083|||4022020081084957002\n",
            )
        with zipfile.ZipFile(oz, "w") as zf:
            zf.writestr("itoth.txt", "")
        fec_src._download = lambda base, cycle, cache_dir, timeout=180.0: (
            (pz if base == "pas2" else oz), None
        )
        p_ofac = work / "pos_ofac.csv"
        p_ofac.write_text('36,"PROBE ENTITY",-0-,"CUBA",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n')
        p_eu = work / "pos_eu.csv"
        p_eu.write_text("Entity_LogicalId;NameAlias_WholeName;Entity_Regulation_Programme\n1001;PROBE ENTITY EU;UKRAINE\n")
        sn_src.download_bulk_file = lambda url, dest, **kw: (
            (p_ofac if "SDN.CSV" in url else p_eu), None
        )
    elif mode == "empty":
        us_src._post = lambda p, pl: (
            {"results": ({g: 0 for g in us_src.AWARD_GROUPS} if "count" in p else []),
             "page_metadata": {"hasNext": False}},
            None,
        )
        ez_pas = work / "empty_pas2.zip"
        ez_oth = work / "empty_oth.zip"
        with zipfile.ZipFile(ez_pas, "w") as zf:
            zf.writestr("itpas2.txt", "")
        with zipfile.ZipFile(ez_oth, "w") as zf:
            zf.writestr("itoth.txt", "")
        fec_src._download = lambda base, cycle, cache_dir, timeout=180.0: (
            (ez_pas if base == "pas2" else ez_oth), None
        )
        e_ofac = work / "empty_ofac.csv"
        e_ofac.write_text('36,"AEROCARIBBEAN AIRLINES",-0-,"CUBA",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n')
        e_eu = work / "empty_eu.csv"
        e_eu.write_text("Entity_LogicalId;NameAlias_WholeName;Entity_Regulation_Programme\n1001;IVANOV IVAN;UKRAINE\n")
        sn_src.download_bulk_file = lambda url, dest, **kw: (
            (e_ofac if "SDN.CSV" in url else e_eu), None
        )

    return saved


def _restore_fetches(saved: list[tuple[object, str, object]]) -> None:
    for mod, attr, orig in saved:
        setattr(mod, attr, orig)


def _fixture_body(spec: SourceSpec, label: str, *, positive: bool, url: str) -> str | bytes:
    if url.endswith("/config"):
        return '{"engines": [{"name": "google", "enabled": true, "categories": ["general"]}]}'
    if spec.driver == "socrata" and label.endswith(".run"):
        if "$select=count" in url:
            return '[{"count": "0"}]'
        return _POS_HTTP_FIXTURES["socrata.query"] if positive else _EMPTY_HTTP_FIXTURES["socrata.query"]
    if spec.driver == "arcgis" and label.endswith(".run"):
        return _POS_HTTP_FIXTURES["arcgis.query"] if positive else _EMPTY_HTTP_FIXTURES["arcgis.query"]
    if spec.driver == "rest" and label.endswith(".run"):
        return _POS_HTTP_FIXTURES["rest.query"] if positive else _EMPTY_HTTP_FIXTURES["rest.query"]
    if label == "socrata.query" and "$select=count" in url:
        return '[{"count": "0"}]'
    if label == "crtsh.search" and "id=1" in url:
        return '[{"id": 1, "common_name": "crt.sh"}]'
    if label == "web.search" and "api.mwmbl.org" in url and "s=wikipedia" in url:
        return json.dumps([{
            "url": "https://en.wikipedia.org/",
            "title": [{"value": "Wikipedia", "is_bold": False}],
            "extract": [{"value": "Free encyclopedia", "is_bold": False}],
            "source": "mwmbl",
        }])

    # Tier 3 bulk HTTP payloads (fara, icij, brokers)
    if label == "fara.search":
        name = "Probe Entity" if positive else "Unrelated Ministry"
        return (
            '"Foreign Principal","Country/Location Represented","Registration Number","Registrant Name"\n'
            f'"{name}","EL SALVADOR","7391","K Street Advisors"\n'
        )
    if label == "icij.search":
        name = "Probe Entity Ltd" if positive else "Unrelated Offshore Ltd"
        return json.dumps({
            "nodes": [{"node_id": "101", "node_type": "entity", "name": name,
                       "jurisdiction": "British Virgin Islands", "source_dataset": "Panama Papers"}],
            "relationships": [],
        })
    if label == "brokers.search":
        name = "Probe Data Inc" if positive else "Unrelated Aggregator LLC"
        return json.dumps({"brokers": [{"state": "CA", "broker_name": name, "reg_number": "CA-99"}]})

    table = _POS_HTTP_FIXTURES if positive else _EMPTY_HTTP_FIXTURES
    return table.get(label, "{}")


def _run_live_canary(spec: SourceSpec, ep_fn: Callable, tmp_root: Path) -> dict:
    """Run one live read-only network canary for `spec`."""
    t0 = time.time()
    s_live = Store(tmp_root / f"live_{spec.name}.db")
    L_live = Limiter(s_live)
    try:
        if spec.name == "openalex":
            from ..sources import openalex
            out = openalex.search("CISA", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "kev":
            from ..sources import kev
            out = kev.search("Ivanti", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "openfda":
            from ..sources import openfda
            out = openfda.search("infusion pump", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "cfpb":
            from ..sources import cfpb
            out = cfpb.search("Experian", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "epa":
            from ..sources import epa
            out = epa.search("Exxon", state="TX", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "wayback":
            from ..sources import wayback
            out = wayback.search("https://www.courtlistener.com/", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "gdelt":
            from ..sources import gdelt
            out = gdelt.search("court", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "crtsh":
            from ..sources import crtsh
            out = crtsh.search("courtlistener.com", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "montana_cadastral":
            out = spec.run("SMITH", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "nyc_contracts":
            out = spec.run("INC", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "web":
            from ..engines import web
            out = web.search("courtlistener", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "chicago_contracts":
            out = spec.run("INC", limit=2, store=s_live, limiter=L_live, use_cache=False)
        elif spec.name == "datagov_catalog":
            out = spec.run("climate", limit=2, store=s_live, limiter=L_live, use_cache=False)
        else:
            out = ep_fn(s_live, L_live)
    except Exception as e:
        return {
            "live_canary_ok": False,
            "live_outcome": f"Exception: {type(e).__name__}: {e}",
            "live_elapsed_ms": int((time.time() - t0) * 1000),
        }

    elapsed = int((time.time() - t0) * 1000)
    ok = isinstance(out, (Hit, VerifiedAbsence))
    return {
        "live_canary_ok": ok,
        "live_outcome": type(out).__name__ + (f" ({len(out.results)} hits)" if isinstance(out, Hit) else ""),
        "live_elapsed_ms": elapsed,
    }


def check_source(name: str, *, live: bool = False) -> dict:
    """Run 6-check manifest + fixture + absence-safety conformance suite on `name`."""
    spec = get_spec(name)
    if spec is None:
        return {
            "name": name,
            "passed": False,
            "errors": [f"Unknown source {name!r}"],
            "checks": {},
        }

    errors: list[str] = []
    manifest_errs = validate_spec(spec)
    manifest_ok = len(manifest_errs) == 0
    errors.extend(manifest_errs)

    tmp_root = Path(tempfile.mkdtemp(prefix=f"es_check_{name}_"))
    eps = _entry_points_for(spec, tmp_root)
    if not eps:
        errors.append(f"No entry points registered for {name!r}")

    transport_ok = True
    ratelimit_ok = True
    malformed_ok = True
    positive_ok = True
    empty_ok = True

    def _dead_fetch(url: str, *, source: str, query: str = "", **kw):
        return None, AccessBlocker(
            query=query,
            coverage=Coverage(queried=[source], errored={source: "conformance-dead-net"}),
            mechanism=Blocker.SERVER_ERROR,
            url=url,
            detail="conformance test: simulated transport failure",
        )

    def _malformed_fetch(url: str, *, source: str, query: str = "", **kw):
        return "{}", None

    class _ExhaustedLimiter:
        wait_for_spacing = False
        wait_max_s = 0.0

        def reserve(self, source, **kw):
            return False, 60, "conformance test: simulated budget exhaustion"

        def note_outcome(self, *a, **kw):
            pass

    class _FastLimiter(Limiter):
        def policy(self, source: str):
            base = core_limits.POLICIES.get(source, core_limits.DEFAULT_POLICY)
            return replace(base, windows=[(60, 100)], min_interval_s=0.0, backoff_base_s=0.0)

    for idx, (label, policy_name, fn) in enumerate(eps):
        # 1. Transport failure check
        saved = _patch_fetches(_dead_fetch, mode="dead", tmp_root=tmp_root, sub_dir=f"d_{idx}")
        try:
            s1 = Store(tmp_root / f"t1_{idx}.db")
            L1 = _FastLimiter(s1)
            out1 = fn(s1, L1)
            if isinstance(out1, VerifiedAbsence):
                transport_ok = False
                errors.append(f"{label}: returned VerifiedAbsence on transport failure")
        finally:
            _restore_fetches(saved)

        # 2. Rate-limit exhaustion check
        saved = _patch_fetches(_dead_fetch, mode="dead", tmp_root=tmp_root, sub_dir=f"r_{idx}")
        try:
            s2 = Store(tmp_root / f"t2_{idx}.db")
            out2 = fn(s2, _ExhaustedLimiter())
            if not isinstance(out2, RateLimited):
                ratelimit_ok = False
                errors.append(f"{label}: expected RateLimited when budget exhausted, got {type(out2).__name__}")
        finally:
            _restore_fetches(saved)

        # 3. Malformed HTTP-200 payload ("{}") check
        saved = _patch_fetches(_malformed_fetch, mode="malformed", tmp_root=tmp_root, sub_dir=f"m_{idx}")
        try:
            s3 = Store(tmp_root / f"t3_{idx}.db")
            L3 = _FastLimiter(s3)
            out3 = fn(s3, L3)
            if isinstance(out3, VerifiedAbsence):
                malformed_ok = False
                errors.append(f"{label}: returned VerifiedAbsence on unexpected '{{}}' payload")
        finally:
            _restore_fetches(saved)

        # Clear any bulk subdirs written during malformed check before running positive/empty
        for sub in ("fara", "icij", "brokers", "sanctions", "fec"):
            dbp = tmp_root / sub / "index.db"
            if dbp.exists():
                dbp.unlink()

        # 4. Positive fixture check -> must produce Hit with >=1 result
        def _pos_fetch(url: str, *, source: str, query: str = "", _lbl=label, **kw):
            return _fixture_body(spec, _lbl, positive=True, url=url), None

        saved = _patch_fetches(_pos_fetch, mode="positive", tmp_root=tmp_root, sub_dir=f"p_{idx}")
        try:
            s4 = Store(tmp_root / f"t4_{idx}.db")
            L4 = _FastLimiter(s4)
            out4 = fn(s4, L4)
            if not isinstance(out4, Hit) or not out4.results:
                positive_ok = False
                errors.append(f"{label}: positive fixture did not yield a non-empty Hit (got {type(out4).__name__}: {getattr(out4, 'detail', '')})")
        finally:
            _restore_fetches(saved)

        for sub in ("fara", "icij", "brokers", "sanctions", "fec"):
            dbp = tmp_root / sub / "index.db"
            if dbp.exists():
                dbp.unlink()

        # 5. Empty-envelope fixture check -> must produce VerifiedAbsence with scopes
        def _emp_fetch(url: str, *, source: str, query: str = "", _lbl=label, **kw):
            return _fixture_body(spec, _lbl, positive=False, url=url), None

        saved = _patch_fetches(_emp_fetch, mode="empty", tmp_root=tmp_root, sub_dir=f"e_{idx}")
        try:
            s5 = Store(tmp_root / f"t5_{idx}.db")
            L5 = _FastLimiter(s5)
            out5 = fn(s5, L5)
            if not isinstance(out5, VerifiedAbsence) or not out5.probes or not (out5.not_searched or out5.caveats):
                empty_ok = False
                errors.append(f"{label}: empty fixture did not yield a scoped VerifiedAbsence (got {type(out5).__name__}: {getattr(out5, 'detail', '')})")
        finally:
            _restore_fetches(saved)

    checks: dict[str, bool] = {
        "manifest_valid": manifest_ok,
        "transport_failure_safe": transport_ok,
        "rate_limit_safe": ratelimit_ok,
        "malformed_payload_safe": malformed_ok,
        "positive_fixture_ok": positive_ok,
        "empty_fixture_ok": empty_ok,
    }
    report: dict = {
        "name": spec.name,
        "origin": spec.origin,
        "driver": spec.driver,
        "policy_name": spec.policy_name,
        "entry_points": [e[0] for e in eps],
        "checks": checks,
        "errors": errors,
    }

    if live and eps:
        live_info = _run_live_canary(spec, eps[0][2], tmp_root)
        checks["live_canary_ok"] = bool(live_info["live_canary_ok"])
        report["live"] = live_info
        if not live_info["live_canary_ok"]:
            errors.append(f"live canary returned {live_info['live_outcome']}")

    report["passed"] = all(checks.values()) and len(errors) == 0
    return report


def check_all_sources(*, live: bool = False) -> list[dict]:
    return [check_source(s.name, live=live) for s in all_specs()]
