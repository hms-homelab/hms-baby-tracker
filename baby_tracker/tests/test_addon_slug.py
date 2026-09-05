"""A failed Supervisor slug lookup must be visible and recoverable.

The Supervisor does not always hand an add-on a SUPERVISOR_TOKEN, even with
`hassio_api: true`. The old code cached the resulting empty slug for the life of
the process behind a bare `suppress(Exception)`, so every link into the add-on
was quietly dead with nothing in the log to say why.
"""
import logging

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Config


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    return Config.load()


def _client(cfg):
    return TestClient(main.create_app(cfg))


def test_no_token_reports_an_empty_slug_and_says_so(app_env, caplog):
    app_env.supervisor_token = None
    with caplog.at_level(logging.WARNING, logger="baby"), _client(app_env) as c:
        assert c.get("/api/config").json()["addon_slug"] == ""
    assert "slug unresolved" in caplog.text
    assert "no Supervisor token" in caplog.text


def test_a_failure_is_not_cached_forever(app_env, monkeypatch):
    """The retry cooldown must expire, so a Supervisor that starts answering is
    picked up without restarting the add-on."""
    app_env.supervisor_token = "tok"
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": {"slug": "abc_baby_tracker"}}

    class _FailResp:
        status_code = 502

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            calls["n"] += 1
            return _FailResp() if calls["n"] == 1 else _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    with _client(app_env) as c:
        assert c.get("/api/config").json()["addon_slug"] == ""      # first: 502
        # Still inside the cooldown: no second call, still empty.
        assert c.get("/api/config").json()["addon_slug"] == ""
        assert calls["n"] == 1
        # Cooldown expires -> it asks again and succeeds.
        monkeypatch.setattr(main.time, "monotonic", lambda: 10 ** 9)
        assert c.get("/api/config").json()["addon_slug"] == "abc_baby_tracker"
        assert calls["n"] == 2
        # ...and a success IS cached (no further calls).
        assert c.get("/api/config").json()["addon_slug"] == "abc_baby_tracker"
        assert calls["n"] == 2


def test_a_thrown_lookup_is_logged_not_swallowed(app_env, monkeypatch, caplog):
    app_env.supervisor_token = "tok"

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise OSError("supervisor unreachable")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    with caplog.at_level(logging.WARNING, logger="baby"), _client(app_env) as c:
        assert c.get("/api/config").json()["addon_slug"] == ""
    assert "supervisor unreachable" in caplog.text
    assert "OSError" in caplog.text
