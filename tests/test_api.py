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
    body = canonical_ini(run_signature="deded0000000dede")
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
        run_signature="abc100000000dead", nickname="x" * 33
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"nickname" in resp.data


def test_submit_notes_too_long_returns_400(client):
    body = canonical_ini(
        run_signature="abc200000000dead", notes="x" * 129
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"notes" in resp.data


def test_submit_uppercase_signatures_normalize_to_lowercase(client):
    """Contract says hex; client may emit either case. The parser must
    lowercase on the way in so browse-route filtering (which lowercases
    URL args) finds the same row. The raw INI is preserved verbatim in
    the archive, so uppercase still appears inside the <pre> block;
    what matters is the stored/queried identifier."""
    body = canonical_ini(
        signature="ABCDEF12",
        run_signature="AAAAAAAABBBBCCCC",
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 200
    # The machine-filter URL must find the submission under the
    # lowercased key. If the parser skipped normalization, the detail
    # page's "all runs from this machine" link would dead-end here.
    machine = client.get("/cerberus/machine/abcdef12")
    assert machine.status_code == 200
    assert b"1 submission" in machine.data
    # Sanity: the uppercase form should NOT resolve, because we store
    # lowercase. (Browse routes also lowercase URL args, but passing a
    # lowercase URL from an uppercase DB row would have been the silent
    # break; this asserts the round-trip.)
    uppercase = client.get("/cerberus/machine/ABCDEF12")
    assert uppercase.status_code == 200
    assert b"1 submission" in uppercase.data  # route-side lowercasing catches it too


def test_submit_malformed_hardware_signature_returns_400(client):
    body = canonical_ini(
        signature="not-a-sig",
        run_signature="aaaaaaaabbbbcccc",
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"hardware_signature" in resp.data


def test_submit_malformed_run_signature_returns_400(client):
    body = canonical_ini(
        run_signature="thisisnotahexrun",  # 16 chars but not hex
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 400
    assert b"run_signature" in resp.data


def test_submit_accepts_40char_run_signature(client):
    """The contract allows either 16-char prefix or 40-char full SHA-1
    digest for run_signature. Ensure the 40-char form is accepted."""
    body = canonical_ini(
        run_signature="cafebabe" * 5,  # 40 hex chars
    )
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 200


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
    # Core CSP: deny-by-default with explicit allowlists.
    assert "default-src 'none'" in csp
    assert "script-src 'none'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    # Permissions-Policy locks down platform features we never use.
    pp = resp.headers.get("Permissions-Policy", "")
    assert "geolocation=()" in pp
    assert "camera=()" in pp


def test_browse_pages_are_cacheable(client):
    # 200 GET on browse pages should carry a short public cache hint.
    resp = client.get("/cerberus/")
    assert resp.status_code == 200
    assert "public" in resp.headers.get("Cache-Control", "")
    assert "max-age=300" in resp.headers.get("Cache-Control", "")


def test_api_responses_not_cached(client):
    # API endpoints must never be cached by the edge.
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "Cache-Control" not in resp.headers or \
        "public" not in resp.headers.get("Cache-Control", "")


def test_post_responses_not_cached(client):
    # POSTs never get the public cache directive.
    resp = client.post(
        "/api/v1/submit",
        data=canonical_ini(run_signature="ca5e0000ca5e0000"),
        content_type="text/plain",
    )
    assert resp.status_code == 200
    assert "public" not in resp.headers.get("Cache-Control", "")


def test_submit_at_max_content_length_succeeds(client):
    # Craft a body exactly 64 KB. Uses a [future_section] pad key that the
    # parser accepts but ignores; the canonical INI provides the required
    # [cerberus] fields. Confirms the MAX_CONTENT_LENGTH=64*1024 ceiling
    # includes the boundary.
    base = canonical_ini(run_signature="aaaabbbb11112222")
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
        data=canonical_ini(run_signature="aaaa11110000aaaa"),
        content_type="text/plain",
    )
    assert first.status_code == 200
    assert first.data.decode("ascii").splitlines()[0] == "aaaaaaaa"

    second = client.post(
        "/api/v1/submit",
        data=canonical_ini(run_signature="bbbb22220000bbbb"),
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
