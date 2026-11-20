"""Generic Esri ArcGIS REST FeatureServer / MapServer driver (`evidence-search arcgis`).

Nearly every county property assessor / cadastral parcel database (such as the
Montana Cadastral ArcGIS server, county recorder parcel layers, and state
environmental / zoning / facility registries) exposes an Esri ArcGIS REST
`FeatureServer/<layerId>` or `MapServer/<layerId>` endpoint supporting
`/query?where=...&outFields=*&returnGeometry=false&f=json`.

Critical epistemic guard: ArcGIS REST servers return HTTP 200 even on failures,
putting `{"error": {"code": 400, "message": "..."}}` inside a 200 response body.
This driver explicitly rejects any `"error"` key and requires a valid
`"features"` list via `json_rows(data, "features")` so an invalid SQL `WHERE`
clause or an expired token never becomes a false `VerifiedAbsence`.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.registry import get_spec, spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "arcgis"


def _parse_arcgis(body: str, *, layer_url: str, source_name: str,
                  title_fields: list[str] | None = None,
                  store: Store | None = None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"arcgis returned non-JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("arcgis response is not a JSON object")
    if "error" in data and data["error"]:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else "?"
        raise ValueError(f"ArcGIS error {code}: {msg}")

    features = json_rows(data, "features", total_key=None)
    if isinstance(features, RecognizedEmpty):
        return features
    out: list[Result] = []
    total = len(features)
    for feat in features:
        if not isinstance(feat, dict):
            continue
        attrs = feat.get("attributes") if isinstance(feat.get("attributes"), dict) else feat
        if not isinstance(attrs, dict):
            continue
        title = ""
        if title_fields:
            parts = [str(attrs.get(f) or "").strip() for f in title_fields if attrs.get(f) not in (None, "")]
            title = " — ".join(parts)[:180]
        if not title:
            parts = []
            for k, v in attrs.items():
                if v in (None, "") or isinstance(v, (dict, list)):
                    continue
                parts.append(f"{k}={v}")
                if len(parts) >= 4:
                    break
            title = " | ".join(parts)[:180] or "ArcGIS feature"
        snippet_parts = [f"{k}: {v}" for k, v in attrs.items()
                         if v not in (None, "") and not isinstance(v, (dict, list))][:7]
        oid = str(
            attrs.get("OBJECTID") or attrs.get("objectid")
            or attrs.get("FID") or attrs.get("PARCELID") or attrs.get("PARCEL_ID") or ""
        ).strip()
        if not oid:
            oid = hashlib.sha256(json.dumps(attrs, sort_keys=True, default=str).encode()).hexdigest()[:12]
        rid = f"{source_name}:{oid}"
        if store is not None:
            try:
                store.put_record(rid, source_name, layer_url, attrs)
            except Exception:
                pass
        out.append(Result(
            url=layer_url,
            title=title,
            snippet=" | ".join(snippet_parts)[:360],
            source=source_name,
            engines=[source_name],
            meta={
                "record_id": rid,
                "layer_url": layer_url,
                "attributes": attrs,
                "total_matches": total,
            },
        ))
    if not out:
        raise ValueError("ArcGIS features list contained no valid feature dicts")
    return out


def query(layer_url: str, text_query: str = "", *,
          field: str | None = None, where: str | None = None,
          out_fields: str = "*", limit: int = 20,
          source_name: str = SOURCE, corpus: str | None = None,
          not_searched: list[str] | None = None, caveats: list[str] | None = None,
          title_fields: list[str] | None = None,
          store: Store | None = None, limiter: Limiter | None = None,
          use_cache: bool = True):
    """Query an Esri ArcGIS REST FeatureServer/MapServer layer."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    clean_layer = (layer_url or "").strip().rstrip("/")
    if clean_layer.endswith("/query"):
        clean_layer = clean_layer[:-6]
    if not clean_layer.startswith(("http://", "https://")):
        return AccessBlocker(
            query=text_query or where or "",
            coverage=Coverage(queried=[source_name], errored={source_name: "bad-layer-url"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=clean_layer or "https://arcgis.com/",
            detail="arcgis requires `--layer-url https://<host>/.../FeatureServer/<layerId>`.",
        )

    where_clean = (where or "").strip()
    q_clean = (text_query or "").strip()
    field_clean = (field or "").strip()
    if not where_clean:
        if q_clean and field_clean:
            escaped = q_clean.replace("'", "''").upper()
            where_clean = f"UPPER({field_clean}) LIKE '%{escaped}%'"
        elif q_clean and "=" in q_clean or " LIKE " in q_clean.upper():
            where_clean = q_clean
        else:
            return AccessBlocker(
                query=q_clean,
                coverage=Coverage(queried=[source_name], errored={source_name: "missing-where"}),
                mechanism=Blocker.WRONG_ID_TYPE,
                url=clean_layer,
                detail="arcgis requires either `--where \"<SQL>\"` or `--field <column> <query>`.",
            )

    params = {
        "where": where_clean,
        "outFields": (out_fields or "*").strip(),
        "returnGeometry": "false",
        "resultRecordCount": str(max(1, min(limit, 1000))),
        "f": "json",
    }
    url = f"{clean_layer}/query?{urlencode(params)}"
    spec_key = source_name if get_spec(source_name) is not None else SOURCE
    _, default_ns, default_cav = spec_bounds(spec_key)
    eff_corpus = corpus or f"ArcGIS REST layer {clean_layer}."
    eff_not_searched = list(not_searched) if not_searched is not None else default_ns
    eff_caveats = list(caveats) if caveats is not None else default_cav

    return run_source(
        where_clean,
        source=source_name,
        url=url,
        parse=lambda body: _parse_arcgis(
            body, layer_url=clean_layer, source_name=source_name,
            title_fields=title_fields, store=store,
        ),
        searched=f"ArcGIS {clean_layer} WHERE {where_clean}",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params={"layer": clean_layer, "where": where_clean, "limit": limit},
        exact_match_supported=True,
        corpus=eff_corpus,
        not_searched=eff_not_searched,
        caveats=eff_caveats,
    )


def run_manifest(manifest: dict, text_query: str = "", *, where: str | None = None,
                 limit: int = 20, store: Store | None = None,
                 limiter: Limiter | None = None, use_cache: bool = True):
    """Execute a declarative ArcGIS JSON manifest plugin."""
    return query(
        layer_url=str(manifest["layer_url"]),
        text_query=text_query,
        field=manifest.get("search_field"),
        where=where,
        out_fields=str(manifest.get("out_fields") or "*"),
        limit=limit,
        source_name=str(manifest.get("name") or SOURCE),
        corpus=manifest.get("corpus"),
        not_searched=manifest.get("not_searched"),
        caveats=manifest.get("caveats"),
        title_fields=manifest.get("title_fields"),
        store=store,
        limiter=limiter,
        use_cache=use_cache,
    )
