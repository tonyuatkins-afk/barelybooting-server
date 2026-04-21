"""Contract test against a real BEK-V409 INI, shaped to v0.7.0.

The INI at ``tests/fixtures/bek_v409_rc2.ini`` is a real CERBERUS run
captured on the 486DX2-66 bench box, hand-upgraded to v0.7.0 INI shape
(see the file's leading comment for the exact upgrade operations).
Every hardware detection value and bench number is real-iron truth;
only the contract-envelope fields were modified.

This test:
  1. POSTs the real INI verbatim via Flask's test client.
  2. Asserts the server accepts it (200, contract-shape response).
  3. Asserts every extracted field reaches the DB row with the exact
     value that CERBERUS emitted.
  4. GETs the detail page, asserts the raw INI is preserved verbatim.
  5. Resubmits to verify the run_signature UNIQUE 409 path.

If the v0.7.0 contract ever drifts away from what CERBERUS actually
emits, this test fails loudly instead of silently storing junk. That
is the whole reason this fixture exists alongside the synthetic
canonical_ini() fixture.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "bek_v409_rc2.ini"


def test_real_bek_v409_round_trip(client, app):
    """Real BEK-V409 INI posts, extracts, renders, and 409s on retry."""
    body = FIXTURE.read_bytes()
    assert body, "fixture should not be empty"

    # --- POST ---
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 200, (
        f"real BEK-V409 INI rejected with {resp.status_code}: "
        f"{resp.data!r}"
    )
    lines = resp.data.decode("ascii").strip().splitlines()
    assert len(lines) == 2
    sub_id = lines[0]
    assert len(sub_id) == 8

    # --- Extracted fields in the DB match the INI ---
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM submissions WHERE id = ?", (sub_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    # Identity. Signatures must round-trip lowercased (they already are,
    # but asserting defends against future parser regressions).
    assert row["hardware_signature"] == "2acd0cf7"
    assert row["run_signature"] == "430d5a098b070f0c"
    assert row["client_version"] == "0.7.0-rc2"
    assert row["ini_format"] == 1

    # CPU. Class was corrected during the hand-upgrade (from vendor
    # string "GenuineIntel" to CPU family "486").
    assert row["cpu_class"] == "486"
    assert row["cpu_detected"] == "Intel i486DX2"

    # FPU. detected wins over friendly per the parser's fallback rule.
    assert row["fpu_detected"] == "integrated-486"

    # Memory: real BEK-V409 values.
    assert row["memory_conv_kb"] == 639
    assert row["memory_ext_kb"] == 63076

    # Cache + bus.
    assert row["cache_present"] == "yes"
    assert row["bus_class"] == "isa16"

    # Video + audio: real hardware identification.
    assert row["video_adapter"] == "vga"
    assert row["video_chipset"] == "S3 Trio64"
    assert row["audio_detected"] == "Sound Blaster AWE64 (CT4500)"

    # BIOS.
    assert row["bios_family"] == "ami"

    # Environment.
    assert row["emulator"] == "none"

    # The raw INI body is archived byte-for-byte (Jinja escapes at
    # render time; storage is verbatim).
    assert row["ini_raw"] == body.decode("ascii")

    # --- GET the detail page ---
    detail = client.get(f"/cerberus/run/{sub_id}")
    assert detail.status_code == 200
    page = detail.data.decode("utf-8")
    assert sub_id in page
    assert "486DX2" in page
    assert "S3 Trio64" in page
    assert "AWE64" in page
    # Raw INI trailer appears in the <pre> block.
    assert "run_signature=430d5a098b070f0c" in page

    # --- Duplicate resubmit hits 409 ---
    resp2 = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp2.status_code == 409
    assert b"duplicate" in resp2.data


def test_real_fixture_machine_filter_finds_run(client):
    """The detail page's 'all runs from this machine' link uses
    row.hardware_signature. The corresponding /cerberus/machine/<sig>
    route must find the run. Regression guard for the normalization
    work: if hardware_signature is ever stored un-lowercased, the
    self-reference link breaks."""
    body = FIXTURE.read_bytes()
    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 200

    # The fixture's signature is "2acd0cf7" (already lowercase).
    machine = client.get("/cerberus/machine/2acd0cf7")
    assert machine.status_code == 200
    assert b"1 submission" in machine.data
    assert b"Intel i486DX2" in machine.data
