"""Shared test helpers: a fake requests.Session so tests never touch the internet."""

from __future__ import annotations

import json

import pytest


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode(errors="replace")

    def json(self):
        return self._payload

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]


class FakeSession:
    """Records every call; `handler(method, url, kwargs)` returns a FakeResponse."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        body = kwargs.get("data")
        if hasattr(body, "read"):
            kwargs["body_bytes"] = body.read()
        self.calls.append((method, url, kwargs))
        return self.handler(method, url, kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip retry back-off waits."""
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def bank_health_env(monkeypatch):
    """Unit tests run as dev unless a test sets another environment (monkeypatch.setenv).
    Real .env values that could point at another environment never leak in."""
    monkeypatch.setenv("BANK_HEALTH_ENV", "dev")
    for name in ("SNOWFLAKE_DATABASE", "DATABRICKS_CATALOG"):
        monkeypatch.delenv(name, raising=False)
