"""API endpoint integration tests via Flask's test client."""

from __future__ import annotations

import os
import tempfile

import pytest

from barelybooting import create_app
from barelybooting.db import init_db

from .fixtures import canonical_ini


@pytest.fixture
def app():
    # Fresh temp DB per test.
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    init_db(path)
    app = create_app({"DATABASE": path, "TESTING": True})
    yield app
    os.unlink(path)


@pytest.fixture
def client(app):
    return app.test_client()


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_submit_returns_two_line_body(client):
    resp = client.post(
        "/api/v1/submit",
        data=canonical_ini(),
        content_type="text/plain",
    )
    assert resp.status_code == 200
    lines = resp.data.decode("ascii").strip().splitlines()
    assert len(lines) == 2
    # Line 1: 8-char hex id
    assert len(lines[0]) == 8
    assert all(c in "0123456789abcdef" for c in lines[0])
    # Line 2: URL ending with the id
    assert lines[1].endswith("/cerberus/run/" + lines[0])


def test_submit_empty_body_returns_400(client):
    resp = client.post(
        "/api/v1/submit", data="", content_type="text/plain"
    )
    assert resp.status_code == 400


def test_submit_missing_required_fields_returns_400(client):
    # No [cerberus] section → no run_signature → 400
    body = "[cpu]\nclass=486\n"
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400


def test_submit_unsupported_ini_format_returns_400(client):
    body = canonical_ini(ini_format=99)
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"ini_format" in resp.data


def test_duplicate_run_signature_returns_409(client):
    body = canonical_ini(run_signature="dedupetest000000")
    first = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert second.status_code == 409


def test_two_distinct_submissions_both_succeed(client):
    a = client.post(
        "/api/v1/submit",
        data=canonical_ini(run_signature="aaaaaaaa00000001"),
        content_type="text/plain",
    )
    b = client.post(
        "/api/v1/submit",
        data=canonical_ini(run_signature="bbbbbbbb00000001"),
        content_type="text/plain",
    )
    assert a.status_code == 200
    assert b.status_code == 200
    a_id = a.data.decode("ascii").strip().splitlines()[0]
    b_id = b.data.decode("ascii").strip().splitlines()[0]
    assert a_id != b_id
