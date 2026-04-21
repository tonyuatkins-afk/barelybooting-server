"""Machine-facing JSON / plain-text endpoints.

Kept deliberately minimal:

* ``POST /api/v1/submit`` — the CERBERUS upload client hits this.
  Contract-strict: accepts raw INI text, returns two lines
  (submission id, public URL). Contract is in the CERBERUS repo at
  ``docs/ini-upload-contract.md``.

* ``GET /api/v1/health`` — trivial liveness check for deployment
  automation. No DB touch, no auth, no logging beyond Flask's default.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from flask import Blueprint, Response, current_app, jsonify, request

from ..db import get_db
from ..ini_parser import parse_ini_text


bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _new_submission_id() -> str:
    """8 hex chars. Server-generated, not derived from content so two
    submissions with identical bodies still get distinct ids."""
    return secrets.token_hex(4)


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@bp.route("/submit", methods=["POST"])
def submit():
    """Accept a CERBERUS.INI upload. See the contract doc for details.

    Return values:
      200 + two-line body on success
      400 on parse failure (missing required [cerberus] fields)
      413 on body over MAX_CONTENT_LENGTH (Flask handles this before us)
      409 on duplicate run_signature (re-submit of the same run)
    """
    raw = request.get_data(as_text=True, cache=False)
    if not raw or not raw.strip():
        return _plain_error(400, "empty body")

    parsed = parse_ini_text(raw)

    # Required fields from the contract.
    if not parsed.run_signature or not parsed.hardware_signature:
        return _plain_error(400, "missing required [cerberus] fields")
    if parsed.ini_format is None:
        return _plain_error(400, "missing ini_format")
    if parsed.ini_format != 1:
        return _plain_error(
            400, f"unsupported ini_format={parsed.ini_format}"
        )

    submission_id = _new_submission_id()
    url = _submission_url(submission_id)

    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO submissions (
                id, hardware_signature, run_signature,
                ini_raw, ini_format, client_version,
                nickname, notes,
                cpu_class, cpu_detected,
                fpu_detected,
                memory_conv_kb, memory_ext_kb,
                cache_present, bus_class,
                video_adapter, video_chipset,
                audio_detected,
                bios_family,
                dhrystones, whetstone_kwips,
                mem_write_kbps, mem_read_kbps, mem_copy_kbps,
                emulator
            ) VALUES (?, ?, ?,
                     ?, ?, ?,
                     ?, ?,
                     ?, ?,
                     ?,
                     ?, ?,
                     ?, ?,
                     ?, ?,
                     ?,
                     ?,
                     ?, ?,
                     ?, ?, ?,
                     ?)
            """,
            (
                submission_id,
                parsed.hardware_signature,
                parsed.run_signature,
                parsed.ini_raw,
                parsed.ini_format,
                parsed.client_version,
                parsed.nickname,
                parsed.notes,
                parsed.cpu_class,
                parsed.cpu_detected,
                parsed.fpu_detected,
                parsed.memory_conv_kb,
                parsed.memory_ext_kb,
                parsed.cache_present,
                parsed.bus_class,
                parsed.video_adapter,
                parsed.video_chipset,
                parsed.audio_detected,
                parsed.bios_family,
                parsed.dhrystones,
                parsed.whetstone_kwips,
                parsed.mem_write_kbps,
                parsed.mem_read_kbps,
                parsed.mem_copy_kbps,
                parsed.emulator,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        # UNIQUE constraint on run_signature → duplicate submission.
        if "run_signature" in msg or "unique" in msg:
            existing = db.execute(
                "SELECT id FROM submissions WHERE run_signature = ?",
                (parsed.run_signature,),
            ).fetchone()
            if existing:
                return _plain_error(
                    409,
                    f"duplicate: already recorded as "
                    f"{_submission_url(existing['id'])}",
                )
        return _plain_error(400, f"db integrity: {e}")

    body = f"{submission_id}\n{url}\n"
    return Response(body, mimetype="text/plain", status=200)


def _submission_url(sub_id: str) -> str:
    base = current_app.config.get(
        "PUBLIC_BASE", "http://127.0.0.1:5000"
    ).rstrip("/")
    return f"{base}/cerberus/run/{sub_id}"


def _plain_error(status: int, message: str) -> Response:
    return Response(
        f"error: {message}\n",
        status=status,
        mimetype="text/plain",
    )
