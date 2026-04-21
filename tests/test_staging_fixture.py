"""Contract test against a real DOSBox Staging emission.

Complements ``test_real_fixture.py`` (real BEK-V409 hardware, hand-
upgraded to v0.7.0 shape) with ``tests/fixtures/staging_dosbox.ini``:
the actual byte stream emitted by CERBERUS v0.7.0-rc2 running in
DOSBox Staging 0.82.2 via ``CERBERUS/devenv/smoketest-staging.conf``.
Nothing reshaped; nothing hand-edited; this is exactly what the DOS
client produces.

If the CERBERUS emitter or the server parser drift apart, this test
fails loud on every CI push. Together with the BEK-V409 fixture,
these two guard the full matrix:

  - real hardware bytes in v0.7.0 shape (bek_v409_rc2.ini)
  - exact v0.7.0 emission against emulator hardware (staging_dosbox.ini)

Known quirks of the Staging fixture (NOT things to fix in tests):

  - `cpu.class=GenuineIntel` is the vendor, mis-stored in the class
    field. Parser normalization lowercases to 'genuineintel', which
    is what the test asserts. Real 486-class identification doesn't
    happen until a bug is filed for CERBERUS's class emission.
  - `nickname=ectation)` is a buffer-leak bug in CERBERUS (captured
    the tail of "mixer chip matches DB expectation)" into the
    nickname field because /NICK wasn't passed). Within contract
    length (32 chars max), so the server accepts it. Tracked as a
    CERBERUS issue; fixture preserves the bug until fixed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "staging_dosbox.ini"


def test_staging_dosbox_round_trip(client, app):
    """Real v0.7.0-rc2 byte stream from DOSBox Staging: POST, extract,
    render, dedup."""
    body = FIXTURE.read_bytes()
    assert body, "fixture should not be empty"

    resp = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp.status_code == 200, (
        f"staging INI rejected with {resp.status_code}: "
        f"{resp.data!r}"
    )
    sub_id = resp.data.decode("ascii").strip().splitlines()[0]
    assert len(sub_id) == 8

    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM submissions WHERE id = ?", (sub_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["hardware_signature"] == "bea58129"
    assert row["run_signature"] == "2551fdc1fb5138db"
    assert row["client_version"] == "0.7.0-rc2"
    assert row["ini_format"] == 1

    # DOSBox Staging identified itself correctly.
    assert row["emulator"] == "dosbox"

    # Known-bug quirk: CPU class still carries vendor string in
    # v0.7.0-rc2. Parser lowercases on the way in.
    assert row["cpu_class"] == "genuineintel"
    assert "486DX" in row["cpu_detected"]

    # FPU detection worked in Staging.
    assert row["fpu_detected"] == "integrated-486"

    # Memory: Staging default is 640 conv + ~15 MB extended.
    assert row["memory_conv_kb"] == 640
    assert row["memory_ext_kb"] > 0

    # Audio: Staging emulates SB16 CT2290 by default.
    assert "Sound Blaster 16" in row["audio_detected"]

    # Bench fields are NULL because /ONLY:DET was used (Staging's FPU
    # can't handle the x87 Whetstone asm kernel; see
    # smoketest-staging.conf header for details).
    assert row["dhrystones"] is None
    assert row["whetstone_kwips"] is None

    # Raw INI archived verbatim.
    assert row["ini_raw"] == body.decode("ascii")

    # Detail page renders.
    detail = client.get(f"/cerberus/run/{sub_id}")
    assert detail.status_code == 200
    page = detail.data.decode("utf-8")
    assert sub_id in page
    assert "dosbox" in page

    # Duplicate detection.
    resp2 = client.post(
        "/api/v1/submit", data=body, content_type="text/plain"
    )
    assert resp2.status_code == 409
