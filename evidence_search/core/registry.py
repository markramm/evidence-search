"""Declarative source-plugin registry (`SourceSpec`).

Every source declares its epistemic contract in one place:
  * `name`: CLI subcommand name (e.g. "usaspending", "courtlistener", "lda")
  * `policy_name`: key in `core.limits.POLICIES`
  * `summary`: one-line description for `evidence-search sources`
  * `corpus`: what the source actually indexes
  * `not_searched`: what a 0-result query here does NOT rule out
  * `caveats`: standing warnings attached to any VerifiedAbsence
  * `exact_match_supported`: True, False, or None (when query-dependent)
  * `counts`: whether `total_matches` from this source is a countable census
  * `requires_key`: optional environment variable / file name for auth
  * `tier`: 1 (keyless REST), 2 (optional/free key REST), 3 (bulk local SQLite),
            4 (local metasearch), 5 (browser-gated)
  * `driver`: "rest" | "bulk" | "socrata" | "arcgis" | "engine"
  * `origin`: "builtin" | "entrypoint" | "manifest"

This lets `evidence-search sources [--json]` and `evidence-search plugin` expose
every source's capabilities and absence boundaries to calling agents before they
run a query, and allows operators to add declarative JSON plugins in
`~/.evidence-search/plugins/*.json` (or `$EVIDENCE_PLUGINS_DIR`) or Python
entry-point plugins under group `"evidence_search.sources"`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Callable

from .limits import POLICIES, Policy, SOURCE_HOSTS


@dataclass(frozen=True)
class Arg:
    """Declarative CLI argument specification for a plugin."""
    flags: tuple[str, ...]
    help: str = ""
    default: object = None
    action: str | None = None
    type: type | None = None
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SourceSpec:
    name: str
    policy_name: str
    summary: str
    corpus: str
    not_searched: tuple[str, ...] = field(default_factory=tuple)
    caveats: tuple[str, ...] = field(default_factory=tuple)
    exact_match_supported: bool | None = True
    counts: bool = True
    requires_key: str | None = None
    tier: int = 1
    driver: str = "rest"
    origin: str = "builtin"
    policy: Policy | None = None
    hosts: dict[str, str] = field(default_factory=dict)
    args: tuple[Arg, ...] = field(default_factory=tuple)
    run: Callable | None = None
    manifest: dict | None = None

    def to_dict(self) -> dict:
        pol = POLICIES.get(self.policy_name)
        return {
            "name": self.name,
            "policy_name": self.policy_name,
            "summary": self.summary,
            "corpus": self.corpus,
            "not_searched": list(self.not_searched),
            "caveats": list(self.caveats),
            "exact_match_supported": self.exact_match_supported,
            "counts": self.counts,
            "requires_key": self.requires_key,
            "tier": self.tier,
            "driver": self.driver,
            "origin": self.origin,
            "limits": {
                "windows": list(pol.windows),
                "min_interval_s": pol.min_interval_s,
                "session_max": pol.session_max,
                "cacheable": pol.cacheable,
            } if pol else None,
        }


REGISTRY: dict[str, SourceSpec] = {}


def validate_spec(spec: SourceSpec) -> list[str]:
    """Validate that a SourceSpec meets evidence-search's epistemic requirements.

    Returns a list of human-readable error strings (empty if valid).
    """
    errors: list[str] = []
    if not spec.name or not isinstance(spec.name, str):
        errors.append("missing `name`")
    if not spec.summary or not isinstance(spec.summary, str):
        errors.append("missing `summary`")
    if not spec.corpus or not isinstance(spec.corpus, str):
        errors.append("missing `corpus` (must state what the endpoint actually covers)")
    if not spec.not_searched or not isinstance(spec.not_searched, (tuple, list)):
        errors.append("missing `not_searched` (must state what a negative does NOT cover)")
    elif any(not isinstance(x, str) or not x.strip() for x in spec.not_searched):
        errors.append("`not_searched` entries must be non-empty strings")
    if spec.exact_match_supported not in (True, False, None):
        errors.append("`exact_match_supported` must be True, False, or None")
    if not isinstance(spec.counts, bool):
        errors.append("`counts` must be a boolean")
    if not spec.policy_name:
        errors.append("missing `policy_name`")
    elif spec.policy_name not in POLICIES and spec.policy is None:
        errors.append(f"`policy_name` {spec.policy_name!r} is not in POLICIES and no `policy` was supplied")
    return errors


def register(spec: SourceSpec, *, validate: bool = True) -> SourceSpec:
    if spec.policy is not None and spec.policy_name not in POLICIES:
        POLICIES[spec.policy_name] = spec.policy
    for host, bucket in (spec.hosts or {}).items():
        entry = (host.lower().strip(), "", bucket or spec.policy_name)
        if entry not in SOURCE_HOSTS:
            SOURCE_HOSTS.append(entry)
    if validate:
        errs = validate_spec(spec)
        if errs:
            raise ValueError(f"Invalid SourceSpec {spec.name!r}: {'; '.join(errs)}")
    REGISTRY[spec.name] = spec
    return spec


def get_spec(name: str) -> SourceSpec | None:
    load_plugins()
    if name in REGISTRY:
        return REGISTRY[name]
    if name == "searxng" and "web" in REGISTRY:
        return REGISTRY["web"]
    for s in REGISTRY.values():
        if s.policy_name == name:
            return s
    return None


def spec_bounds(
    name: str,
    *,
    corpus: str = "",
    not_searched: tuple[str, ...] | list[str] = (),
    caveats: tuple[str, ...] | list[str] = (),
) -> tuple[str, list[str], list[str]]:
    """Return `(corpus, not_searched, caveats)` from the registry with fallback defaults."""
    s = get_spec(name)
    if s is None:
        return corpus, list(not_searched), list(caveats)
    return s.corpus or corpus, list(s.not_searched), list(s.caveats)


def all_specs() -> list[SourceSpec]:
    load_plugins()
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def plugins_dir(override: Path | str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = (os.environ.get("EVIDENCE_PLUGINS_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".evidence-search" / "plugins"


def spec_from_manifest(raw: dict, *, origin: str = "manifest") -> SourceSpec:
    """Construct and validate a SourceSpec from a declarative JSON manifest dict.

    Supported drivers in declarative manifests:
      * `"socrata"`: requires `"domain"` and `"dataset_id"`
      * `"arcgis"`:  requires `"layer_url"`
      * `"rest"`:    requires `"url_template"` or `"endpoint"`
    """
    from urllib.parse import urlsplit as _urlsplit

    if not isinstance(raw, dict):
        raise ValueError("Plugin manifest must be a JSON object")
    name = str(raw.get("name") or "").strip()
    driver = str(raw.get("driver") or "socrata").strip().lower()
    if driver not in ("socrata", "arcgis", "rest"):
        raise ValueError(f"Unsupported declarative manifest driver {driver!r} (expected 'socrata', 'arcgis', or 'rest')")
    if driver == "socrata" and (not raw.get("domain") or not raw.get("dataset_id")):
        raise ValueError("Socrata plugin manifest requires 'domain' and 'dataset_id'")
    if driver == "arcgis" and not raw.get("layer_url"):
        raise ValueError("ArcGIS plugin manifest requires 'layer_url'")
    if driver == "rest" and not (raw.get("url_template") or raw.get("endpoint")):
        raise ValueError("REST plugin manifest requires 'url_template' or 'endpoint'")

    policy_name = str(raw.get("policy_name") or name or driver).strip()
    pol = None
    if isinstance(raw.get("limits"), dict):
        lim = raw["limits"]
        wins: list[tuple[float, int]] = []
        if lim.get("per_second"):
            wins.append((1, int(lim["per_second"])))
        if lim.get("per_minute"):
            wins.append((60, int(lim["per_minute"])))
        if lim.get("per_hour"):
            wins.append((3600, int(lim["per_hour"])))
        if lim.get("per_day"):
            wins.append((86400, int(lim["per_day"])))
        if not wins:
            wins = [(60, 20)]
        pol = Policy(
            windows=wins,
            min_interval_s=float(lim.get("min_interval_s", 0.5)),
            note=str(raw.get("summary") or ""),
        )
    elif policy_name not in POLICIES:
        pol = Policy(
            windows=[(60, 20), (3600, 400)],
            min_interval_s=0.5,
            note=str(raw.get("summary") or ""),
        )

    hosts: dict[str, str] = {}
    if driver == "socrata" and raw.get("domain"):
        hosts[str(raw["domain"]).lower().strip()] = policy_name
    elif driver in ("arcgis", "rest"):
        raw_url = str(raw.get("layer_url") or raw.get("endpoint") or raw.get("url_template") or "").strip()
        host = (_urlsplit(raw_url).hostname or "").lower()
        if host:
            hosts[host] = policy_name

    def _runner(query: str = "", *, where: str | None = None, limit: int = 20,
                store=None, limiter=None, use_cache: bool = True):
        if driver == "socrata":
            from ..sources import socrata as soc_mod
            return soc_mod.run_manifest(raw, query, where=where, limit=limit,
                                        store=store, limiter=limiter, use_cache=use_cache)
        if driver == "rest":
            from ..sources import rest_manifest as rest_mod
            return rest_mod.run_manifest(raw, query, where=where, limit=limit,
                                         store=store, limiter=limiter, use_cache=use_cache)
        from ..sources import arcgis as arc_mod
        return arc_mod.run_manifest(raw, query, where=where, limit=limit,
                                    store=store, limiter=limiter, use_cache=use_cache)

    return SourceSpec(
        name=name,
        policy_name=policy_name,
        summary=str(raw.get("summary") or f"{driver} plugin {name}").strip(),
        corpus=str(raw.get("corpus") or "").strip(),
        not_searched=tuple(str(x).strip() for x in (raw.get("not_searched") or []) if str(x).strip()),
        caveats=tuple(str(x).strip() for x in (raw.get("caveats") or []) if str(x).strip()),
        exact_match_supported=raw.get("exact_match_supported", True),
        counts=bool(raw.get("counts", True)),
        requires_key=raw.get("requires_key"),
        tier=int(raw.get("tier", 1)),
        driver=driver,
        origin=origin,
        policy=pol,
        hosts=hosts,
        run=_runner,
        manifest=dict(raw),
    )


def load_plugins(directory: Path | str | None = None, *, force: bool = False) -> list[SourceSpec]:
    """Load external entry-point plugins and declarative JSON manifest plugins.

    Safe to call repeatedly; broken manifests are skipped without crashing the CLI.
    """
    loaded: list[SourceSpec] = []

    # 1. Python package entry points: group="evidence_search.sources"
    try:
        eps = importlib.metadata.entry_points(group="evidence_search.sources")
    except Exception:
        eps = ()
    for ep in eps:
        if ep.name in REGISTRY and not force:
            continue
        try:
            obj = ep.load()
            specs = obj() if callable(obj) else obj
            if isinstance(specs, SourceSpec):
                specs = [specs]
            for sp in (specs or []):
                if isinstance(sp, SourceSpec):
                    register(sp)
                    loaded.append(sp)
        except Exception:
            continue

    # 2. Shipped declarative JSON manifests inside evidence_search/plugins/*.json
    shipped_dir = Path(__file__).resolve().parent.parent / "plugins"
    for dir_path, origin_tag in ((shipped_dir, "builtin-manifest"), (plugins_dir(directory), "manifest")):
        try:
            if dir_path.is_dir():
                for mf in sorted(dir_path.glob("*.json")):
                    try:
                        raw = json.loads(mf.read_text())
                        sp = spec_from_manifest(raw, origin=f"{origin_tag}:{mf.name}")
                        if sp.name in REGISTRY and (REGISTRY[sp.name].origin == "builtin" or not force):
                            continue
                        register(sp)
                        loaded.append(sp)
                    except Exception:
                        continue
        except OSError:
            pass

    return loaded


def scaffold_manifest(
    name: str,
    *,
    driver: str,
    domain: str | None = None,
    dataset_id: str | None = None,
    layer_url: str | None = None,
    endpoint: str | None = None,
    rows_path: str | None = None,
    total_path: str | None = None,
    search_field: str | None = None,
    summary: str | None = None,
    target_dir: Path | str | None = None,
) -> Path:
    """Create and validate a starter declarative JSON manifest plugin."""
    clean_name = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name.strip().lower()).strip("_")
    if not clean_name:
        raise ValueError("Plugin name must contain alphanumeric characters")
    drv = driver.strip().lower()
    if drv not in ("socrata", "arcgis", "rest"):
        raise ValueError(f"Unsupported driver {driver!r}; choose 'socrata', 'arcgis', or 'rest'")

    if drv == "socrata":
        if not domain or not dataset_id:
            raise ValueError("Socrata plugin scaffold requires --domain and --dataset")
        manifest = {
            "name": clean_name,
            "driver": "socrata",
            "policy_name": clean_name,
            "domain": domain.strip(),
            "dataset_id": dataset_id.strip(),
            "search_field": (search_field or "vendor_name").strip(),
            "title_fields": [(search_field or "vendor_name").strip()],
            "summary": summary or f"Socrata portal plugin ({domain}/{dataset_id})",
            "corpus": f"Socrata open-data dataset {dataset_id} on {domain}.",
            "not_searched": [
                f"Other datasets on {domain} not matching {dataset_id}",
                "Records redacted or excluded from the agency open-data export",
            ],
            "caveats": [
                "Search spellings reflect portal records; use `--where` for exact SoQL column filters.",
            ],
            "exact_match_supported": None,
            "counts": True,
            "tier": 1,
            "limits": {"per_minute": 20, "per_hour": 400, "min_interval_s": 0.5},
        }
    elif drv == "rest":
        if not endpoint:
            raise ValueError("REST plugin scaffold requires --endpoint")
        manifest = {
            "name": clean_name,
            "driver": "rest",
            "policy_name": clean_name,
            "endpoint": endpoint.strip(),
            "rows_path": (rows_path or "results").strip(),
            **({"total_path": total_path.strip()} if total_path else {}),
            "summary": summary or f"JSON REST plugin ({clean_name})",
            "corpus": f"JSON REST endpoint at {endpoint.strip()}.",
            "not_searched": [
                "Records excluded or paginated beyond the API endpoint query window",
            ],
            "caveats": [
                "Query semantics match the upstream JSON API defaults.",
            ],
            "exact_match_supported": False,
            "counts": bool(total_path),
            "tier": 1,
            "limits": {"per_minute": 20, "per_hour": 400, "min_interval_s": 0.5},
        }
    else:
        if not layer_url:
            raise ValueError("ArcGIS plugin scaffold requires --layer-url")
        manifest = {
            "name": clean_name,
            "driver": "arcgis",
            "policy_name": clean_name,
            "layer_url": layer_url.strip(),
            "search_field": (search_field or "OwnerName").strip(),
            "title_fields": [(search_field or "OwnerName").strip()],
            "summary": summary or f"ArcGIS REST layer plugin ({clean_name})",
            "corpus": f"Esri ArcGIS REST layer at {layer_url.strip()}.",
            "not_searched": [
                "Neighboring GIS services or unrecorded filings not synced to this ArcGIS layer",
            ],
            "caveats": [
                "Owner/entity spellings match the GIS layer attributes; use `--where` for custom SQL filters.",
            ],
            "exact_match_supported": True,
            "counts": True,
            "tier": 1,
            "limits": {"per_minute": 20, "per_hour": 400, "min_interval_s": 0.5},
        }

    sp = spec_from_manifest(manifest, origin=f"manifest:{clean_name}.json")
    register(sp)
    out_dir = plugins_dir(target_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{clean_name}.json"
    out_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return out_path


# --- Built-in source specifications ---

_BUILTIN_SPECS = [
    SourceSpec(
        name="news",
        policy_name="news_rss",
        summary="Google News RSS feed search (keyless discovery; redirect URLs)",
        corpus="Google News RSS index of recent news articles.",
        not_searched=(
            "paywalled archives not indexed by Google News",
            "historical print archives prior to web indexing",
            "primary public records (dockets, contracts, filings)",
        ),
        caveats=(
            "Google News RSS returns opaque redirect URLs (citable=False); resolve the article on the publisher's site before citing.",
        ),
        exact_match_supported=True,
        counts=False,
        tier=1,
    ),
    SourceSpec(
        name="web",
        policy_name="web",
        summary="General web search (zero-daemon Mwmbl default; optional API key or SearXNG)",
        corpus="General web search (keyless Mwmbl REST API by default; optional Brave, Serper, Tavily, or SearXNG override).",
        not_searched=(
            "paywalled or login-walled sites",
            "deep-web databases not indexed by general web engines (PACER, state SOS postbacks)",
        ),
        caveats=(
            "Upstream web engines largely ignore quoted phrases; result sets are fuzzy and totals are not countable.",
            "Keyless Mwmbl coverage is smaller than commercial indices; configure BRAVE_API_KEY, SERPER_API_KEY, TAVILY_API_KEY, or SEARXNG_URL if needed.",
        ),
        exact_match_supported=False,
        counts=False,
        tier=4,
        driver="engine",
    ),
    SourceSpec(
        name="oscn",
        policy_name="oscn",
        summary="Oklahoma State Courts Network (OSCN) dockets and party search",
        corpus="OSCN dockets for the requested Oklahoma county/court and year filter.",
        not_searched=(
            "non-OSCN Oklahoma municipal courts",
            "sealed or expunged records",
            "federal courts in Oklahoma (N.D./E.D./W.D. Okla. — use `courtlistener`)",
        ),
        caveats=(
            "Party search matches the indexed spelling only; check maiden names or spelling variants separately.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=5,
    ),
    SourceSpec(
        name="courtlistener",
        policy_name="courtlistener",
        summary="Free Law Project CourtListener opinions, RECAP dockets, and docket-watch",
        corpus="CourtListener RECAP archive of crowd-sourced PACER dockets/filings and published case-law opinions.",
        not_searched=(
            "PACER dockets or filings that no RECAP user has ever purchased/uploaded",
            "sealed federal filings",
            "most state trial-court dockets",
        ),
        caveats=(
            "Unquoted queries are tokenized and stemmed; quote multi-word names or phrases when counting.",
            "In `--docket` watch mode, RECAP reflects the docket only as of its `Last Updated` timestamp, not real-time PACER.",
        ),
        exact_match_supported=None,
        counts=True,
        requires_key="COURTLISTENER_TOKEN (optional; ~/.config/courtlistener/token)",
        tier=2,
    ),
    SourceSpec(
        name="propublica",
        policy_name="propublica_disclosures",
        summary="ProPublica Trump-administration appointee financial disclosures (OGE Form 278e)",
        corpus="ProPublica's Trump-team executive-branch appointee financial disclosures index.",
        not_searched=(
            "career civil-service disclosures not in ProPublica's appointee dataset",
            "congressional financial disclosures (House Clerk / Senate eFD)",
            "blind trusts or spousal assets exempt from OGE Form 278e schedule reporting",
        ),
        caveats=(
            "Matches appointee names and disclosure text as filed; check spelling variants and holding-company LLC names.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="usaspending",
        policy_name="usaspending",
        summary="USAspending.gov federal prime contracts, IDV task orders, grants, and modifications",
        corpus="USAspending.gov prime award and transaction endpoints (FPDS-NG & FABS mirror, FY2008-present).",
        not_searched=(
            "subawards and pass-through subcontracts (prime awards only by default)",
            "classified or intelligence-community contracts exempt from FPDS reporting",
            "Other Transaction Authorities (OTAs) not reported to FPDS",
            "pre-FY2008 awards",
        ),
        caveats=(
            "DoD procurement awards carry a statutory 90-day reporting lag before appearing in USAspending.",
            "IDV parent headers carry $0 obligated; actual dollars live on child delivery/task orders (`--idv <PIID>`).",
            "`base_and_all_options_value` is not mirrored by USAspending's search endpoint; check FPDS/SAM.gov for ceiling values.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="crossref",
        policy_name="crossref",
        summary="Crossref scholarly DOI resolver and bibliographic metadata search",
        corpus="Crossref works index (~150M scholarly records with registered DOIs).",
        not_searched=(
            "works without a Crossref DOI (working papers, some books, agency reports)",
            "article full text (Crossref indexes bibliographic metadata and abstracts only)",
        ),
        caveats=(
            "DOI lookup is exact and authoritative; title/keyword search is FUZZY and totals are not countable.",
        ),
        exact_match_supported=None,
        counts=False,
        tier=1,
    ),
    SourceSpec(
        name="fedreg",
        policy_name="federal_register",
        summary="U.S. Federal Register rules, proposed rules, notices, and presidential documents",
        corpus="Federal Register (federalregister.gov API v1, 1994-present).",
        not_searched=(
            "agency guidance documents or memos not published in the Federal Register",
            "individual contract J&As posted only on SAM.gov",
            "pre-1994 Federal Register issues not digitized in the v1 structured index",
        ),
        caveats=(
            "Unquoted queries are relevance-ranked across full text; read `total_matches` rather than page length.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="docs",
        policy_name="docs",
        summary="Vendor documentation via llms.txt indexes (claude-code, claude-api)",
        corpus="Published llms.txt documentation indexes for the configured vendor site.",
        not_searched=(
            "unindexed blog posts, changelog commits, or private enterprise documentation",
        ),
        caveats=(
            "Searches the llms.txt index titles/summaries unless `--fetch-top` is used to inspect page bodies.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="lda",
        policy_name="lda",
        summary="U.S. Senate Lobbying Disclosure Act (LDA) LD-1 registrations and LD-2 activity reports",
        corpus="U.S. Senate LDA filings database (lda.senate.gov API v1, 1999-present, LD-1 and LD-2 filings).",
        not_searched=(
            "LD-203 semi-annual political contribution reports (`/api/v1/contributions/`)",
            "Foreign Agents Registration Act (FARA) filings with DOJ (`evidence-search fara`)",
            "state and municipal lobbying registrations",
            "grassroots lobbying or coalition spending below LDA registration thresholds",
            "direct campaign contributions (`evidence-search fec`)",
        ),
        caveats=(
            "`client_name` and `registrant_name` perform case-insensitive substring matching; check parent and subsidiary names.",
        ),
        exact_match_supported=True,
        counts=True,
        requires_key="LDA_API_KEY (optional; anonymous tier works at ~15 req/min)",
        tier=1,
    ),
    SourceSpec(
        name="fec",
        policy_name="fec",
        summary="Federal Election Commission (FEC) campaign finance (bulk SQLite index first, OpenFEC Schedule A fallback)",
        corpus="FEC campaign-finance bulk files (pas2/oth/cm/cn, when indexed locally) and OpenFEC `/v1/schedules/schedule_a/` itemized receipts.",
        not_searched=(
            "unitemized individual contributions <= $200 cumulative per cycle",
            "501(c)(4) 'dark money' donor rolls not disclosed to the FEC",
            "state and local campaign-finance filings",
            "independent expenditures (Schedule E) unless queried via committee disbursements",
        ),
        caveats=(
            "Contributor and employer names are self-reported on paper/electronic filings and contain abbreviations and typos.",
            "Set `FEC_API_KEY` for API queries; the fallback `DEMO_KEY` is throttled at ~30-40 req/hr across all users.",
        ),
        exact_match_supported=True,
        counts=True,
        requires_key="FEC_API_KEY (optional if using local bulk SQLite index; falls back to DEMO_KEY)",
        tier=2,
        driver="bulk",
    ),
    SourceSpec(
        name="samgov",
        policy_name="samgov",
        summary="SAM.gov federal contract opportunities, solicitation notices, and public attachments",
        corpus="SAM.gov contract opportunities index (sgs/v1/search?index=opp and opps/v2/opportunities).",
        not_searched=(
            "awarded contracts and task orders (use `usaspending`)",
            "classified or national-security-exempt procurements",
            "SAM.gov entity-registration details (JS-only SPA; use `browser` tier)",
        ),
        caveats=(
            "Quote phrases for exact matching; unquoted queries match keywords across notice text.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="nonprofit",
        policy_name="nonprofit",
        summary="ProPublica Nonprofit Explorer IRS Form 990 index, financials, and filing PDFs",
        corpus="ProPublica Nonprofit Explorer API v2 (IRS Form 990, 990-EZ, and 990-PF filings and EO BMF index).",
        not_searched=(
            "Schedule B donor names and addresses (redacted by the IRS from public Form 990 disclosures)",
            "churches and religious organizations exempt from filing Form 990",
            "grants RECEIVED by a nonprofit (Form 990 Schedule I lists grants MADE by the filer; search the grantor's 990)",
            "Officer/director names or Schedule I recipient text inside unextracted 990 PDFs (pass the filing `pdf_url` to `evidence-search extract`)",
        ),
        caveats=(
            "Multiple unrelated organizations can share the same or similar names across states; always verify the EIN (`strein`) and state.",
            "ProPublica's name search uses fuzzy stemming (`DonorsTrust` can match `Donorsrus Inc`); check the returned organization name.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="sec",
        policy_name="sec",
        summary="SEC EDGAR full-text filing search (EFTS) and company submission history by CIK/ticker",
        corpus="U.S. SEC EDGAR filings index (2001-present full text via EFTS; company submissions via data.sec.gov).",
        not_searched=(
            "privately held companies without SEC reporting obligations (unless named in a public filer's exhibit or 8-K)",
            "foreign issuers not trading on U.S. exchanges or exempt under Rule 12g3-2(b)",
            "pre-2001 paper filings not digitized in EDGAR EFTS",
        ),
        caveats=(
            "Quote multi-word entity names in full-text search (`'\"CoreCivic\"'`) for exact phrase matching.",
            "Company-name lookup can match multiple CIKs (parent, subsidiary, historical registrant); verify the 10-digit CIK.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="sanctions",
        policy_name="sanctions",
        summary="U.S. Treasury OFAC SDN and EU Consolidated Financial Sanctions bulk index",
        corpus="U.S. Treasury OFAC Specially Designated Nationals (SDN) list and EU Financial Sanctions Consolidated list (indexed locally in SQLite).",
        not_searched=(
            "OFAC Consolidated Non-SDN lists (SSI, CAPTA, NS-MBS) unless included in SDN",
            "UK OFSI, UN Security Council, or national autonomous sanctions lists outside OFAC and EU",
            "Entities blocked by operation of law under OFAC's '50 Percent Rule' (owned >=50% by a blocked person) that are not explicitly named on the SDN list",
        ),
        caveats=(
            "Transliteration across Cyrillic/Arabic/CJK scripts can vary; fuzzy matches (`possible_match`, Jaro-Winkler >= 0.85) are leads that must be verified against DOB, passport/tax ID, and address.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=3,
        driver="bulk",
    ),
    SourceSpec(
        name="fara",
        policy_name="fara",
        summary="U.S. DOJ Foreign Agents Registration Act (FARA) registrants and foreign principals",
        corpus="U.S. Department of Justice FARA eFile bulk/API registry of active and terminated foreign principals and registrants (efile.fara.gov).",
        not_searched=(
            "Domestic lobbying registrations under the Lobbying Disclosure Act (use `evidence-search lda`)",
            "Unregistered foreign lobbying or covert influence operations not filed with DOJ FARA",
            "Commercial/diplomatic activities exempt from FARA under 22 U.S.C. § 613 (e.g. bona fide commercial or LDA-exempt filings)",
        ),
        caveats=(
            "Foreign principals and registrants may be indexed under formal ministry names, embassy names, or local corporate acronyms; check country and name variants.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=3,
        driver="bulk",
    ),
    SourceSpec(
        name="wayback",
        policy_name="wayback",
        summary="Internet Archive Wayback Machine CDX historical URL capture index and SHA-1 digests",
        corpus="Internet Archive Wayback Machine CDX index (`web.archive.org/cdx/search/cdx`).",
        not_searched=(
            "Live web pages that were never crawled by the Internet Archive or were excluded via robots.txt / takedown request",
            "Other web archives (archive.today, Perma.cc, Library of Congress web archives)",
            "Full text inside archived captures (pass the returned `raw_archive_url` to `evidence-search extract` to read or diff capture contents)",
        ),
        caveats=(
            "CDX queries match exact URLs by default (`--match-type exact`); pass `--match-type prefix` or `--match-type domain` to sweep subpaths.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="openalex",
        policy_name="openalex",
        summary="OpenAlex scholarly works, author ORCIDs, institution RORs, funding grants, and retraction flags",
        corpus="OpenAlex scholarly index (~250M works, authors, institutions, funders, and Retraction Watch flags via `api.openalex.org`).",
        not_searched=(
            "Proprietary court expert reports or agency consulting memos never published as scholarly works or preprints",
            "Full text of paywalled journal articles (OpenAlex indexes metadata, open-access PDF links, citations, and retraction flags)",
        ),
        caveats=(
            "Author name searches without an ORCID can conflate homonymous researchers; verify institution affiliation and ORCID.",
            "Any work with `is_retracted=true` is flagged in `entity_caveat` — check the retraction notice before citing.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="socrata",
        policy_name="socrata",
        summary="Generic Socrata / Tyler Data & Insights SODA v2 open-data portal driver (state/city checkbooks, contracts, registries)",
        corpus="The specified Socrata open-data dataset (`https://<domain>/resource/<dataset_id>.json`).",
        not_searched=(
            "Other datasets on the same Socrata portal not matching `--dataset <4x4-id>`",
            "Records withheld from the agency's open-data export or lagging behind the portal's last-updated date",
        ),
        caveats=(
            "`$q=` performs full-text keyword search across all text columns; pass `--where <SoQL>` for exact column filtering.",
        ),
        exact_match_supported=None,
        counts=True,
        requires_key="SOCRATA_APP_TOKEN (optional; increases throttling limits on Socrata portals)",
        tier=1,
        driver="socrata",
    ),
    SourceSpec(
        name="arcgis",
        policy_name="arcgis",
        summary="Generic Esri ArcGIS REST FeatureServer / MapServer driver (county property parcels, cadastral GIS, state registries)",
        corpus="The specified Esri ArcGIS REST FeatureServer/MapServer layer (`<layer_url>/query`).",
        not_searched=(
            "Other layers in the same ArcGIS service or neighboring county GIS servers",
            "Unrecorded deeds or parcel transfers not yet synced to the county/state GIS layer",
        ),
        caveats=(
            "ArcGIS SQL `WHERE` clauses depend on the layer's field names; inspect returned `attributes` keys or pass `--field <column>`.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=1,
        driver="arcgis",
    ),
    SourceSpec(
        name="kev",
        policy_name="kev",
        summary="CISA Known Exploited Vulnerabilities (KEV) catalog & NIST NVD CVE 2.0",
        corpus="CISA Known Exploited Vulnerabilities JSON catalog (federal BOD 22-01) and NIST NVD CVE 2.0 API.",
        not_searched=(
            "Unpatched zero-days or proprietary vendor advisories without a published CVE identifier",
            "Non-KEV CVEs unless `--nvd` is passed",
        ),
        caveats=(
            "Vendor and product names in CISA KEV vary (e.g. 'Ivanti' vs 'Pulse Secure'); check both `--vendor` and keyword queries.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="crtsh",
        policy_name="crtsh",
        summary="crt.sh public Certificate Transparency (CT) X.509 certificate log search",
        corpus="crt.sh PostgreSQL index of public Certificate Transparency X.509 certificates.",
        not_searched=(
            "Self-signed internal certificates or private enterprise CAs never logged to public CT logs",
            "Unencrypted HTTP hosts or DNS names with no TLS certificates issued",
        ),
        caveats=(
            "Pass a wildcard prefix (`%.example.gov`) to include subdomains; expired certificates remain in CT logs unless `--active-only` is set.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="epa",
        policy_name="epa",
        summary="EPA ECHO (Enforcement and Compliance History Online) regulated facilities & violations",
        corpus="EPA ECHO regulated facilities database covering Clean Air, Clean Water, and RCRA hazardous waste sites.",
        not_searched=(
            "Unregulated facilities or small generators exempt from EPA ECHO reporting",
            "State/local environmental citations not synced to EPA ICIS / ECHO",
        ),
        caveats=(
            "Facility names reflect permit-holder strings; check both street address and parent corporate names.",
        ),
        exact_match_supported=False,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="openfda",
        policy_name="openfda",
        summary="OpenFDA medical-device recalls, 510(k) clearances, drug enforcement, and FAERS adverse events",
        corpus="U.S. FDA OpenFDA JSON API (`/device/recall`, `/device/510k`, `/drug/enforcement`, `/drug/event`).",
        not_searched=(
            "Pending premarket approvals (PMA) or unreleased FDA Form 483 observations not indexed in OpenFDA",
            "Under-reported post-market incidents not filed to FAERS / MAUDE",
        ),
        caveats=(
            "OpenFDA search uses Lucene syntax; subsidiary and DBA manufacturer names vary across filings.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="cfpb",
        policy_name="cfpb",
        summary="CFPB Consumer Complaint Database search and narratives",
        corpus="Consumer Financial Protection Bureau public Consumer Complaint Database (2011-present).",
        not_searched=(
            "Complaints sent exclusively to state Attorneys General or FTC Sentinel without forwarding to CFPB",
            "Consumer narratives where the consumer opted out of public publication",
        ),
        caveats=(
            "Complaint narratives are unverified consumer accounts published after company verification of a commercial relationship.",
        ),
        exact_match_supported=None,
        counts=True,
        tier=1,
    ),
    SourceSpec(
        name="icij",
        policy_name="icij",
        summary="ICIJ Offshore Leaks bulk SQLite index (Panama/Paradise/Pandora Papers, relationships)",
        corpus="ICIJ Offshore Leaks Database bulk CSV/ZIP archive (entities, officers, intermediaries, 1-hop relationships).",
        not_searched=(
            "Offshore entities in non-leaked corporate registries (BVI, Cayman, Delaware, Wyoming)",
            "Post-leak beneficial ownership changes not present in the published ICIJ document tranches",
        ),
        caveats=(
            "Transliteration variants and nominee directors can obscure beneficial owners; appearance in ICIJ Offshore Leaks does not itself establish illegality.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=3,
        driver="bulk",
    ),
    SourceSpec(
        name="brokers",
        policy_name="brokers",
        summary="Four-state statutory data-broker registries (CA CPPA, VT SOS, TX SOS, OR DCBS)",
        corpus="California CPPA, Vermont SOS, Texas SOS, and Oregon DCBS statutory data-broker registration lists indexed locally.",
        not_searched=(
            "Data brokers exempt under state revenue/record thresholds or operating solely with direct-relationship exemptions",
            "U.S. states without a statutory data-broker registration law",
        ),
        caveats=(
            "Data brokers frequently register under parent holding companies while operating consumer websites under DBAs.",
        ),
        exact_match_supported=True,
        counts=True,
        tier=3,
        driver="bulk",
    ),
    SourceSpec(
        name="gdelt",
        policy_name="gdelt",
        summary="GDELT DOC 2.0 global news article discovery API (uncountable discovery tier)",
        corpus="GDELT Project DOC 2.0 ArtList index of global news and broadcast transcripts.",
        not_searched=(
            "Paywalled print/wire archives not crawled by GDELT",
            "Primary public records (court dockets, contracts, corporate registries)",
        ),
        caveats=(
            "GDELT DOC 2.0 caps article lists at 250 records and emphasizes machine-translated international media; totals are not a census.",
        ),
        exact_match_supported=False,
        counts=False,
        tier=1,
    ),
]

for _s in _BUILTIN_SPECS:
    register(_s)
load_plugins()
