# Design note: a source-plugin system for evidence-search

Status: implemented (`core/registry.py`, `core/conformance.py`, `sources/socrata.py`,
`sources/arcgis.py`). Originally drafted 2026-08-20 after Mark raised the idea;
extended to support declarative manifest plugins, entry-point discovery, and an
automated absence-safety conformance harness (`evidence-search plugin check`).

## The problem, measured

Adding one source previously touched **five places**, three of which were edits
to existing shared files rather than new files:

1. `sources/<name>.py` — the client (new file, fine)
2. `cli.py` — an argparse subparser
3. `cli.py` — a dispatch branch in `main()`
4. `core/limits.py` — a `POLICIES` entry
5. `README.md` + `SKILL.md` — documentation, by hand

## The finding that makes this cheap

**The self-documentation already exists.** Every migrated source declares its
own epistemic limits — `corpus`, `not_searched`, `caveats`,
`exact_match_supported`, and `counts`:

| field | what it carries | e.g. crossref |
|---|---|---|
| `corpus` | what this endpoint actually covers | "Crossref works index (~150M records), matched LOOSELY on title. Totals are not measurements." |
| `not_searched` | the honest boundary of a negative | "works with no Crossref DOI registered", "full text -- Crossref indexes metadata only" |
| `caveats` | why a negative here might still be wrong | "FUZZY title search ... a differently-worded title would not be found. Prefer a DOI lookup where one exists." |
| `exact_match_supported` | whether a negative can ever be absolute | `False` |

That is a better self-description than most MCP tool definitions, because it
describes *what the tool cannot tell you*, not just what it accepts.

## Implemented Architecture

### 1. `SourceSpec` Registry (`core/registry.py`)

Every source registers a frozen `SourceSpec`:

```python
@dataclass(frozen=True)
class SourceSpec:
    name: str
    policy_name: str
    summary: str
    corpus: str
    not_searched: tuple[str, ...]
    caveats: tuple[str, ...] = ()
    exact_match_supported: bool | None = True
    counts: bool = True
    requires_key: str | None = None
    tier: int = 2
    driver: str = "rest"           # "rest" | "bulk" | "socrata" | "arcgis" | "engine"
    origin: str = "builtin"        # "builtin" | "entrypoint" | "manifest"
    policy: Policy | None = None
    hosts: dict[str, str] = field(default_factory=dict)
    run: Callable | None = None
    manifest: dict | None = None
```

When `register(spec)` is called:
- If `spec.policy` is provided, it is automatically added to `POLICIES` in
  `core/limits.py` so every plugin participates in the shared atomic SQLite
  reservation ledger (`~/.evidence-search/store.db`).
- If `spec.hosts` is provided, those hostnames are bound to `spec.policy_name`
  in `SOURCE_HOSTS` so `extract` and `browser` calls against the plugin's
  domain draw from the same rate-limit budget.

### 2. Three Reusable Driver Templates

1. **REST / Socrata / ArcGIS (`core/source.py`, `sources/socrata.py`, `sources/arcgis.py`):**
   - `run_source` handles caching, atomic limiter reservation, `json_rows()`,
     `RecognizedEmpty`, and `absence_meta(..., shape_verified=...)`.
   - `sources/socrata.py` wraps any Socrata / Tyler SODA v2 portal
     (`https://<domain>/resource/<4x4>.json`).
   - `sources/arcgis.py` wraps any Esri ArcGIS REST `FeatureServer` or
     `MapServer` layer (`.../query?f=json`), catching ArcGIS's HTTP-200
     `{"error": ...}` envelopes so they never become false absences.
2. **Bulk SQLite (`core/bulk.py`):**
   - Downloads CSV/TSV/ZIP bulk files, records SHA-256 + fetch timestamp in
     `bulk_sources`, indexes locally into SQLite, and bounds every
     `VerifiedAbsence` with the local snapshot timestamp and hash.
   - Powers `sanctions`, `fec` (bulk mode), and `fara` (bulk mode).
3. **Browser-Gated Public Records (`core/browser.py` + `core/gates.py`):**
   - Scoped `ALLOWED_HOSTS` entry with a required public-records justification
     and humanomation gate capture validation.

### 3. Declarative JSON Manifest Plugins & Entry-Point Discovery

Plugins can be added in two ways without editing `evidence-search`:
1. **Declarative JSON manifests** in `~/.evidence-search/plugins/*.json` (or
   `$EVIDENCE_PLUGINS_DIR`): a 20-line JSON file declaring `"driver": "socrata"`
   or `"driver": "arcgis"`, the endpoint/dataset ID, field mappings, rate-limit
   policy, and epistemic boundaries (`corpus`, `not_searched`, `caveats`).
2. **Python package entry points** under the group `"evidence_search.sources"`
   in `pyproject.toml`, returning a `SourceSpec` (or list of `SourceSpec`s).

### 4. Conformance Harness (`evidence-search plugin check`)

`core/conformance.py` turns the absence-safety invariants from
`tests/test_every_source_absence.py` into a first-class CLI command:

```bash
evidence-search plugin list
evidence-search plugin check            # audits every registered source & plugin
evidence-search plugin check openalex   # audits a single source
```

Every plugin is tested against:
1. **Epistemic manifest completeness** (`corpus`, `not_searched`, valid `Policy`
   in `POLICIES`, explicit `counts` and `exact_match_supported`).
2. **Transport failure immunity** (network failure returns `AccessBlocker` or
   `RateLimited`, never `VerifiedAbsence`).
3. **Rate-limit exhaustion immunity** (exhausted budget returns `RateLimited`,
   never `VerifiedAbsence`).
4. **Malformed payload immunity** (an unexpected `{}` or HTML soft-block page
   returns `AccessBlocker(PARSE_FAILED)`, never `VerifiedAbsence`).
