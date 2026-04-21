"""Browse-page integration tests.

``client`` fixture comes from ``tests/conftest.py``.
"""

from __future__ import annotations

from .fixtures import canonical_ini


def _submit(client, **kw):
    resp = client.post(
        "/api/v1/submit",
        data=canonical_ini(**kw),
        content_type="text/plain",
    )
    assert resp.status_code == 200
    return resp.data.decode("ascii").strip().splitlines()[0]


def test_index_empty(client):
    resp = client.get("/cerberus/")
    assert resp.status_code == 200
    assert b"No submissions" in resp.data


def test_index_shows_submission(client):
    sub_id = _submit(client, run_signature="0000000011111111", nickname="tony")
    resp = client.get("/cerberus/")
    assert resp.status_code == 200
    assert sub_id.encode() in resp.data
    assert b"tony" in resp.data


def test_cpu_class_filter(client):
    _submit(client, run_signature="0000000022222222", cpu_class="486")
    _submit(client, run_signature="0000000033333333", cpu_class="386")
    resp = client.get("/cerberus/cpu/486")
    assert resp.status_code == 200
    assert b"1 submission" in resp.data


def test_machine_filter(client):
    _submit(
        client,
        signature="deadbeef",
        run_signature="0000000044444444",
    )
    _submit(
        client,
        signature="deadbeef",
        run_signature="0000000055555555",
    )
    _submit(
        client,
        signature="cafef00d",
        run_signature="0000000066666666",
    )
    resp = client.get("/cerberus/machine/deadbeef")
    assert resp.status_code == 200
    assert b"2 submissions" in resp.data


def test_run_detail(client):
    sub_id = _submit(
        client,
        run_signature="0000000077777777",
        notes="the great hall",
    )
    resp = client.get(f"/cerberus/run/{sub_id}")
    assert resp.status_code == 200
    assert b"486DX2-66" in resp.data
    assert b"the great hall" in resp.data
    # Raw INI should be present in the detail view
    assert b"run_signature=0000000077777777" in resp.data


def test_run_detail_404(client):
    resp = client.get("/cerberus/run/no-such-id")
    assert resp.status_code == 404


def test_pagination_page_past_end_clamps_to_last(client):
    # One submission means one page. ?page=999 should still render the
    # submission (clamped to page 1), not an empty "No submissions"
    # screen. Without the clamp, OFFSET=24950 would return zero rows.
    sub_id = _submit(client, run_signature="a9ec1a1a0000abba")
    resp = client.get("/cerberus/?page=999")
    assert resp.status_code == 200
    assert b"1 submission" in resp.data
    assert sub_id.encode() in resp.data
    assert b"No submissions" not in resp.data


def test_unknown_filter(client):
    _submit(client, run_signature="0000000088888888", cpu_class=None,
            cpu_detected=None)
    resp = client.get("/cerberus/unknown")
    assert resp.status_code == 200
    assert b"1 submission" in resp.data
