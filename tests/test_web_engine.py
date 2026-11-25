"""Unit tests for the zero-daemon general web engine (`evidence_search.engines.web`)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import types

import httpx
import pytest

from evidence_search.cli import EXIT_HIT, main
from evidence_search.core import http as core_http
from evidence_search.core.limits import Limiter
from evidence_search.core.results import AccessBlocker, Blocker, Hit, RateLimited, VerifiedAbsence
from evidence_search.core.store import Store
from evidence_search.engines import searxng as sx_mod
from evidence_search.engines import web


def _kit(tmp_path: pathlib.Path | None = None):
    root = tmp_path or pathlib.Path(tempfile.mkdtemp())
    s = Store(root / "web_test.db")
    return s, Limiter(s)


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("test attempted an unexpected network call")

    monkeypatch.setattr(core_http, "fetch", _boom)


def test_zero_daemon_web_client_providers(tmp_path, monkeypatch):
    """Zero-daemon `web` client covers keyless Mwmbl, commercial API keys, and SearXNG override."""
    for var in ("SEARXNG_URL", "BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    assert web.resolve_provider() == "mwmbl"
    s, L = _kit(tmp_path)

    # 1. Keyless Mwmbl default: flattens highlight spans and deduplicates canonical URLs
    mwmbl_payload = json.dumps([
        {
            "url": "https://example.org/doc?utm_source=x",
            "title": [{"value": "Court", "is_bold": True}, {"value": " Record", "is_bold": False}],
            "extract": [{"value": "Docket summary", "is_bold": False}],
            "source": "mwmbl",
        },
        {
            "url": "https://example.org/doc",
            "title": [{"value": "Duplicate URL", "is_bold": False}],
            "extract": [{"value": "Dup", "is_bold": False}],
            "source": "mwmbl",
        },
    ])
    monkeypatch.setattr(web, "fetch", lambda *a, **k: (mwmbl_payload, None))
    hit = web.search("court record", store=s, limiter=L, use_cache=False)
    assert isinstance(hit, Hit)
    assert len(hit.results) == 1
    assert hit.results[0].title == "Court Record"
    assert hit.results[0].engines == ["mwmbl"]

    # 2. Bare [] only certifies VerifiedAbsence when the wikipedia control probe is healthy
    def _empty_mwmbl(url, *, source, query="", **kw):
        if "s=wikipedia" in url:
            return mwmbl_payload, None
        return "[]", None

    monkeypatch.setattr(web, "fetch", _empty_mwmbl)
    absence = web.search("zzzzunlikely", store=s, limiter=L, use_cache=False)
    assert isinstance(absence, VerifiedAbsence)
    assert absence.probes

    monkeypatch.setattr(web, "fetch", lambda *a, **k: ("[]", None))
    refused = web.search("zzzzbroken", store=s, limiter=L, use_cache=False)
    assert isinstance(refused, AccessBlocker)

    # 3. Missing API key on explicit commercial provider returns AUTH_WALL blocker
    blocked_key = web.search("test", provider="brave", store=s, limiter=L, use_cache=False)
    assert isinstance(blocked_key, AccessBlocker)
    assert blocked_key.mechanism is Blocker.AUTH_WALL

    # 4. Auto-select Brave when BRAVE_API_KEY is set
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-token")
    assert web.resolve_provider() == "brave"
    seen_headers: list[dict | None] = []

    def _brave_fetch(url, *, source, query="", headers=None, **kw):
        seen_headers.append(headers)
        assert "api.search.brave.com" in url
        return json.dumps({
            "type": "search",
            "web": {"results": [{"url": "https://brave.example/1", "title": "Brave Hit", "description": "desc"}]},
        }), None

    monkeypatch.setattr(web, "fetch", _brave_fetch)
    brave_hit = web.search("brave query", store=s, limiter=L, use_cache=False)
    assert isinstance(brave_hit, Hit)
    assert brave_hit.results[0].engines == ["brave"]
    assert seen_headers[0]["X-Subscription-Token"] == "brave-test-token"

    # 5. Auto-select Serper POST transport when only SERPER_API_KEY is set
    monkeypatch.delenv("BRAVE_API_KEY")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test-token")
    assert web.resolve_provider() == "serper"
    seen_posts: list[tuple[str, dict]] = []

    def _serper_post(url, payload, *, source, query="", headers=None):
        seen_posts.append((url, payload))
        return json.dumps({
            "searchParameters": {"q": query},
            "organic": [{"link": "https://serper.example/1", "title": "Serper Hit", "snippet": "ok", "position": 1}],
        }), None

    monkeypatch.setattr(web, "_post_json", _serper_post)
    serper_hit = web.search("serper query", store=s, limiter=L, use_cache=False)
    assert isinstance(serper_hit, Hit)
    assert serper_hit.results[0].engines == ["serper"]
    assert seen_posts[0][0] == web.SERPER_URL and seen_posts[0][1]["q"] == "serper query"

    # 6. Auto-select Tavily POST transport when only TAVILY_API_KEY is set
    monkeypatch.delenv("SERPER_API_KEY")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test-token")
    assert web.resolve_provider() == "tavily"
    monkeypatch.setattr(
        web,
        "_post_json",
        lambda url, payload, **kw: (
            json.dumps({
                "query": payload["query"],
                "results": [{"url": "https://tavily.example/1", "title": "Tavily Hit", "content": "ok", "score": 0.9}],
            }),
            None,
        ),
    )
    tavily_hit = web.search("tavily query", store=s, limiter=L, use_cache=False)
    assert isinstance(tavily_hit, Hit)
    assert tavily_hit.results[0].engines == ["tavily"]

    # 7. SEARXNG_URL / --base / --provider searxng delegates to searxng.search
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8888")
    assert web.resolve_provider() == "searxng"
    delegated: list[tuple[str, str | None]] = []

    def _fake_sx(q, categories="general", pageno=1, base=None, **kw):
        delegated.append((q, base))
        return Hit(query=q, coverage=brave_hit.coverage, results=[], probes=[])

    monkeypatch.setattr(sx_mod, "search", _fake_sx)
    web.search("fallback query", store=s, limiter=L, use_cache=False)
    web.search("explicit base", base="http://127.0.0.1:9999", store=s, limiter=L, use_cache=False)
    assert delegated == [("fallback query", None), ("explicit base", "http://127.0.0.1:9999")]


def test_web_resolve_provider_precedence_and_invalid_provider(tmp_path, monkeypatch):
    """Precedence: explicit provider > base/SEARXNG_URL > BRAVE > SERPER > TAVILY > mwmbl."""
    monkeypatch.setenv("TAVILY_API_KEY", "tv")
    assert web.resolve_provider() == "tavily"
    monkeypatch.setenv("SERPER_API_KEY", "sp")
    assert web.resolve_provider() == "serper"
    monkeypatch.setenv("BRAVE_API_KEY", "br")
    assert web.resolve_provider() == "brave"
    assert web.resolve_provider(base="http://localhost:8888") == "searxng"
    assert web.resolve_provider("mwmbl", base="http://localhost:8888") == "mwmbl"

    with pytest.raises(ValueError, match="Unknown web provider"):
        web.resolve_provider("duckduckgo-invalid")

    s, L = _kit(tmp_path)
    out = web.search("q", provider="bogus", store=s, limiter=L, use_cache=False)
    assert isinstance(out, AccessBlocker)
    assert out.mechanism is Blocker.WRONG_ID_TYPE


def test_web_mwmbl_span_flattening_dedup_and_wikipedia_control_query(tmp_path, monkeypatch):
    assert web._flatten_mwmbl_spans(None) == ""
    assert web._flatten_mwmbl_spans("Plain title") == "Plain title"
    assert web._flatten_mwmbl_spans(["Part A", {"value": " Part B"}, None]) == "Part A Part B"

    seen_urls: list[str] = []

    def _wiki_control(url, *, source, query="", **kw):
        seen_urls.append(url)
        if "s=python" in url:
            return json.dumps([{"url": "https://www.python.org/", "title": "Python"}]), None
        return "[]", None

    monkeypatch.setattr(web, "fetch", _wiki_control)
    s, L = _kit(tmp_path)
    out = web.search("wikipedia", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert any("s=python" in u for u in seen_urls)


def test_web_brave_pagination_empty_variants_and_malformed_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    s, L = _kit(tmp_path)

    captured_urls: list[str] = []

    def _brave_page2(url, *, source, query="", headers=None, **kw):
        captured_urls.append(url)
        return json.dumps({
            "type": "search",
            "query": {"original": query},
            "web": {"results": [{"url": "https://ex.org/p2", "title": "Page 2", "description": "d"}]},
        }), None

    monkeypatch.setattr(web, "fetch", _brave_page2)
    hit = web.search("test", pageno=3, provider="brave", store=s, limiter=L, use_cache=False)
    assert isinstance(hit, Hit)
    assert "offset=2" in captured_urls[0]

    # Empty envelope variant: type="search", web absent
    monkeypatch.setattr(
        web,
        "fetch",
        lambda *a, **k: (json.dumps({"type": "search", "query": {"original": "nothing"}}), None),
    )
    abs_out = web.search("nothing", provider="brave", store=s, limiter=L, use_cache=False)
    assert isinstance(abs_out, VerifiedAbsence)

    # Bare {} and {"error": ...} must NEVER certify absence
    for bad in ("{}", '{"error": "rate exceeded"}'):
        monkeypatch.setattr(web, "fetch", lambda *a, b=bad, **k: (b, None))
        blk = web.search("bad", provider="brave", store=s, limiter=L, use_cache=False)
        assert isinstance(blk, AccessBlocker)


def test_web_serper_and_tavily_post_json_transport_errors_and_news_mode(tmp_path, monkeypatch):
    # Test real _post_json mapping for 429, TimeoutException, HTTPError, and 401
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: types.SimpleNamespace(
            status_code=429, headers={"Retry-After": "17"}, text="Slow down"
        ),
    )
    body, outcome = web._post_json("https://example.test", {}, source="web", query="q")
    assert body is None and isinstance(outcome, RateLimited)
    assert outcome.retry_after_s == 17 and outcome.detail.startswith("HTTP 429")

    def _timeout(*a, **k):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _timeout)
    _, out_to = web._post_json("https://example.test", {}, source="web", query="q")
    assert isinstance(out_to, RateLimited)

    def _conn_err(*a, **k):
        raise httpx.ConnectError("dns failed")

    monkeypatch.setattr(httpx, "post", _conn_err)
    _, out_err = web._post_json("https://example.test", {}, source="web", query="q")
    assert isinstance(out_err, AccessBlocker) and out_err.mechanism is Blocker.SERVER_ERROR

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: types.SimpleNamespace(status_code=401, headers={}, text="Unauthorized"),
    )
    _, out_auth = web._post_json("https://example.test", {}, source="web", query="q")
    assert isinstance(out_auth, AccessBlocker) and out_auth.mechanism is Blocker.AUTH_WALL

    # Test news mode + empty results for Serper & Tavily
    s, L = _kit(tmp_path)
    monkeypatch.setenv("SERPER_API_KEY", "sp-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tv-key")
    seen_payloads: list[dict] = []

    def _mock_post(url, *, source="", json=None, **kw):
        seen_payloads.append(json)
        empty = {"searchParameters": {}, "organic": []} if "serper" in url else {"query": "q", "results": []}
        return types.SimpleNamespace(status_code=200, headers={}, text=__import__("json").dumps(empty))

    monkeypatch.setattr(httpx, "post", lambda url, **kw: _mock_post(url, **kw))
    abs_s = web.search("border news", categories="news", provider="serper", store=s, limiter=L, use_cache=False)
    abs_t = web.search("border news", categories="news", provider="tavily", store=s, limiter=L, use_cache=False)
    assert isinstance(abs_s, VerifiedAbsence) and isinstance(abs_t, VerifiedAbsence)
    assert seen_payloads[0]["type"] == "news"
    assert seen_payloads[1]["topic"] == "news"


def test_cli_web_subcommand_end_to_end(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cli_web.db"
    monkeypatch.setenv("EVIDENCE_DB", str(db))
    monkeypatch.setattr(
        web,
        "fetch",
        lambda *a, **k: (
            json.dumps([
                {
                    "url": "https://www.courtlistener.com/",
                    "title": [{"value": "CourtListener", "is_bold": False}],
                    "extract": [{"value": "Free legal research", "is_bold": False}],
                    "source": "mwmbl",
                }
            ]),
            None,
        ),
    )
    rc = main(["--json", "web", "courtlistener", "--provider", "mwmbl", "--limit", "5"])
    assert rc == EXIT_HIT
    data = json.loads(capsys.readouterr().out)
    assert data["results"][0]["url"] == "https://www.courtlistener.com/"


@pytest.fixture(autouse=True)
def _isolated_key_files(tmp_path_factory, monkeypatch):
    """Keep a real ~/.config/<provider>/api_key from changing provider selection in tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-empty")))


def test_web_key_file_fallback_and_env_precedence(tmp_path, monkeypatch):
    """A key file under <config>/<provider>/api_key is used when the env var is unset; env wins."""
    for var in ("SEARXNG_URL", "BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg = tmp_path / "cfg"
    (cfg / "brave").mkdir(parents=True)
    (cfg / "brave" / "api_key").write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))

    assert web._provider_key("brave") == "file-token"
    assert web.resolve_provider() == "brave"
    assert web._provider_key("serper") == ""

    monkeypatch.setenv("BRAVE_API_KEY", "env-token")
    assert web._provider_key("brave") == "env-token"


def test_web_brave_freshness_and_goggles_params(tmp_path, monkeypatch):
    """freshness and goggles reach Brave's query string; a local goggle file is sent inline."""
    from urllib.parse import parse_qs, urlparse
    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    s, L = _kit(tmp_path)
    captured: list[str] = []

    def _brave(url, *, source, query="", headers=None, **kw):
        captured.append(url)
        return json.dumps({"type": "search", "query": {"original": query},
                           "web": {"results": [{"url": "https://ex.gov/a", "title": "A", "description": "d"}]}}), None

    monkeypatch.setattr(web, "fetch", _brave)
    gfile = tmp_path / "tcp.goggle"
    gfile.write_text("! name: TCP\n$boost=3,site=courtlistener.com\n$discard,site=nytimes.com\n", encoding="utf-8")
    hit = web.search("x", provider="brave", store=s, limiter=L, use_cache=False,
                     freshness="2026-11-20to2026-11-25", goggles=[str(gfile), "$boost=2,site=gov"])
    assert isinstance(hit, Hit)
    q = parse_qs(urlparse(captured[0]).query)
    assert q["freshness"] == ["2026-11-20to2026-11-25"]
    assert q["goggles"][0] == "$boost=3,site=courtlistener.com\n$discard,site=nytimes.com"
    assert q["goggles"][1] == "$boost=2,site=gov"


def test_web_freshness_validation_and_non_brave_refusal(tmp_path, monkeypatch):
    """A malformed freshness is refused; freshness/goggles on a non-Brave provider are refused, not ignored."""
    for var in ("SEARXNG_URL", "BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s, L = _kit(tmp_path)
    with pytest.raises(ValueError):
        web._check_freshness("last-week")
    assert web._check_freshness("pw") == "pw"
    with pytest.raises(ValueError):
        web.search("x", provider="mwmbl", store=s, limiter=L, use_cache=False, freshness="pw")
    with pytest.raises(ValueError):
        web._goggle_values(["a", "b", "c", "d"])
