"""Tests for SDD-005: a configurable OpenAI-compatible base URL, and
`hidden_modules` (UI-only module visibility).

Origin: discussion #8. The two invariants worth pinning down are (a) an unset
option changes nothing for existing installs, and (b) hiding is presentation
only, so the API and the Baby Remote keep accepting hidden event types.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config, _as_list
from app import llm


# --- 1. OpenAI-compatible base URL --------------------------------------

@pytest.mark.parametrize("configured,expected", [
    # Unset: unchanged behaviour, still OpenAI.
    ("", "https://api.openai.com/v1/chat/completions"),
    # Host only (what a service's docs often show).
    ("https://openrouter.ai/api", "https://openrouter.ai/api/v1/chat/completions"),
    # Base with /v1, the most commonly documented form.
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
    # Trailing slash.
    ("http://homeassistant.local:11434/v1/", "http://homeassistant.local:11434/v1/chat/completions"),
    # Ollama Cloud.
    ("https://ollama.com/v1", "https://ollama.com/v1/chat/completions"),
    # Full path pasted straight from the docs: left alone.
    ("https://litellm.lan/v1/chat/completions", "https://litellm.lan/v1/chat/completions"),
    # Surrounding whitespace from a copy/paste.
    ("  https://api.groq.com/openai/v1  ", "https://api.groq.com/openai/v1/chat/completions"),
])
def test_openai_endpoint_resolution(configured, expected):
    cfg = Config(summary_openai_url=configured)
    assert llm.openai_endpoint(cfg) == expected


def test_openai_posts_to_configured_host(monkeypatch):
    """The request must actually go to the configured base, with the bearer."""
    seen = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": " recap text "}}]}

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            seen["model"] = (json or {}).get("model")
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    cfg = Config(summary_provider="openai",
                 summary_openai_url="https://openrouter.ai/api/v1",
                 summary_api_key="sk-test",
                 summary_model="meta-llama/llama-3.3-70b-instruct")

    import asyncio
    out = asyncio.run(llm.generate(cfg, "prompt"))

    assert out == "recap text"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["model"] == "meta-llama/llama-3.3-70b-instruct"


def test_openai_default_still_openai():
    """No option set: the shipped behaviour is untouched."""
    assert llm.openai_endpoint(Config()) == "https://api.openai.com/v1/chat/completions"


# --- 2. hidden_modules ---------------------------------------------------

def test_hidden_modules_default_empty():
    assert Config().hidden_modules == []


def test_as_list_normalizes_and_drops_unknowns():
    got = _as_list(["Tab.Contractions", " feed.bottle ", "tab.contractions",
                    "not_a_module", "tab.baby"])
    # deduped, lowercased, trimmed; unknown ids and the un-hideable Baby tab gone
    assert got == ["tab.contractions", "feed.bottle"]


def test_as_list_accepts_comma_string_and_junk():
    assert _as_list("tab.health,sleep") == ["tab.health", "sleep"]
    assert _as_list(None) == []
    assert _as_list(42) == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    def _make(hidden="", default_tab="baby"):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MQTT_HOST", "")
        monkeypatch.setenv("HIDDEN_MODULES", hidden)
        monkeypatch.setenv("DEFAULT_TAB", default_tab)
        from app import main
        return TestClient(main.create_app(Config.load()))
    return _make


def test_config_echoes_hidden_modules(client):
    with client("tab.contractions,feed.bottle") as c:
        body = c.get("/api/config").json()
    assert body["hidden_modules"] == ["tab.contractions", "feed.bottle"]


def test_config_hidden_modules_empty_by_default(client):
    with client() as c:
        assert c.get("/api/config").json()["hidden_modules"] == []


def test_default_tab_falls_back_when_hidden(client):
    """Landing on a hidden tab would open the app to a blank screen."""
    with client(hidden="tab.contractions", default_tab="contractions") as c:
        assert c.get("/api/config").json()["default_tab"] == "baby"


def test_default_tab_kept_when_visible(client):
    with client(hidden="tab.growth", default_tab="contractions") as c:
        assert c.get("/api/config").json()["default_tab"] == "contractions"


def test_hidden_event_type_still_logs(client):
    """Hiding is UI only. The Baby Remote and existing automations publish the
    same event types regardless, so the API must keep accepting them."""
    with client(hidden="feed.bottle,sleep") as c:
        r = c.post("/api/event", json={"event_type": "feed", "event_subtype": "bottle"})
        assert r.status_code in (200, 201)
        log = c.get("/api/log").json()
        assert any(e["event_type"] == "feed" and e["event_subtype"] == "bottle"
                   for e in log["entries"])


def test_config_yaml_catalog_matches_code():
    """The schema dropdown and HIDEABLE_MODULES must not drift apart."""
    from pathlib import Path
    from app.config import HIDEABLE_MODULES
    raw = (Path(__file__).resolve().parents[1] / "config.yaml").read_text()
    line = next(ln for ln in raw.splitlines() if ln.strip().startswith("- list(tab.get_ready"))
    listed = line.strip().removeprefix("- list(").removesuffix(")?").split("|")
    assert listed == list(HIDEABLE_MODULES)
