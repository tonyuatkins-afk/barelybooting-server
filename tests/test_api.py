"""API endpoint integration tests via Flask's test client.

``app`` and ``client`` fixtures come from ``tests/conftest.py``.
"""

from __future__ import annotations

from .fixtures import canonical_ini


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


def test_submit_nickname_too_long_returns_400(client):
    body = canonical_ini(
        run_signature="nicktest00000001", nickname="x" * 33
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"nickname" in resp.data


def test_submit_notes_too_long_returns_400(client):
    body = canonical_ini(
        run_signature="notetest00000001", notes="x" * 129
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"notes" in resp.data


def test_submit_non_ascii_body_returns_400(client):
    # non-ASCII byte (0xE9) should be rejected, not silently mangled
    body = canonical_ini().encode("ascii") + b"\n; cafe\xe9\n"
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"non-ascii" in resp.data


def test_submit_rejects_json_content_type(client):
    resp = client.post(
        "/api/v1/submit",
        data=canonical_ini(),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert b"content-type" in resp.data


def test_security_headers_set(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src 'none'" in csp
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_submit_at_max_content_length_succeeds(client):
    # Craft a body exactly 64 KB. Uses a [future_section] pad key that the
    # parser accepts but ignores; the canonical INI provides the required
    # [cerberus] fields. Confirms the MAX_CONTENT_LENGTH=64*1024 ceiling
    # includes the boundary.
    base = canonical_ini(run_signature="maxsize000000001")
    padding_needed = 64 * 1024 - len(base) - len("\n[pad]\npad=\n")
    pad_value = "x" * padding_needed
    body = base + "\n[pad]\npad=" + pad_value + "\n"
    assert len(body) == 64 * 1024
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 200


def test_submit_over_max_content_length_returns_413(client):
    # One byte over the ceiling. Werkzeug short-circuits before the
    # route runs, so we just need the size to exceed 64 KB.
    body = canonical_ini() + ("x" * (64 * 1024))
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 413


def test_submission_id_retries_on_collision(client, monkeypatch):
    """When _new_submission_id returns a value that already exists, the
    insert path must retry with a fresh id rather than 400ing the caller.
    We force a collision by monkeypatching the generator to return a
    fixed value for the first two calls."""
    from barelybooting.routes import api as api_mod

    # First submission gets id "aaaaaaaa". Second tries "aaaaaaaa" (PK
    # collision), then "bbbbbbbb" (succeeds).
    ids = iter(["aaaaaaaa", "aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(api_mod, "_new_submission_id", lambda: next(ids))

    first = client.post(
        "/api/v1/submit",
        data=canonical_ini(run_signature="retry0000000001a"),
        content_type="text/plain",
    )
    assert first.status_code == 200
    assert first.data.decode("ascii").splitlines()[0] == "aaaaaaaa"

    second = client.post(
        "/api/v1/submit",
        data=canonical_ini(run_signature="retry0000000002b"),
        content_type="text/plain",
    )
    assert second.status_code == 200
    assert second.data.decode("ascii").splitlines()[0] == "bbbbbbbb"


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
