"""Machine-facing JSON / plain-text endpoints.

Kept deliberately minimal:

* ``POST /api/v1/submit``, the CERBERUS upload client hits this.
  Contract-strict: accepts raw INI text, returns two lines
  (submission id, public URL). Contract is in the CERBERUS repo at
  ``docs/ini-upload-contract.md``.

* ``GET /api/v1/health``, trivial liveness check for deployment
  automation. No DB touch, no auth, no logging beyond Flask's default.
"""

from __future__ import annotations

import secrets
import sqlite3
from flask import Blueprint, Response, current_app, jsonify, request

from ..db import get_db
from ..extensions import limiter
from ..ini_parser import parse_ini_text


bp = Blueprint("api", __name__, url_prefix="/api/v1")


# Contract-enforced maxima. The DOS client caps these client-side; we
# enforce again server-side so a hand-crafted POST cannot inject unbounded
# text into the public browse pages.
MAX_NICKNAME_LEN = 32
MAX_NOTES_LEN = 128

# Retry budget for the 32-bit submission_id collision ring. At 4 billion
# possible values, birthday-collision 50% probability lands at ~65k rows;
# expected retries per insert stay sub-1 until then. A hard ceiling here
# keeps a pathological DB from spinning forever.
ID_RETRY_LIMIT = 8


def _new_submission_id() -> str:
    """8 hex chars. Server-generated, not derived from content so two
    submissions with identical bodies still get distinct ids."""
    return secrets.token_hex(4)


@bp.route("/health", methods=["GET"])
@limiter.exempt
def health():
    return jsonify({"status": "ok"})


@bp.route("/submit", methods=["POST"])
@limiter.limit("30 per hour; 5 per minute")
def submit():
    """Accept a CERBERUS.INI upload. See the contract doc for details.

    Return values:
      200 + two-line body on success
      400 on parse failure (missing required [cerberus] fields)
      413 on body over MAX_CONTENT_LENGTH (Flask handles this before us)
      409 on duplicate run_signature (re-submit of the same run)
      429 on rate-limit (Flask-Limiter handles this before us)
    """
    # Contract is text/plain; reject obvious multipart / form / JSON to
    # avoid Flask parsing work on bodies we would only throw out.
    ctype = (request.content_type or "").split(";", 1)[0].strip().lower()
    if ctype and not ctype.startswith("text/"):
        return _plain_error(400, f"bad content-type: {ctype}")

    raw_bytes = request.get_data(cache=False, as_text=False)
    if not raw_bytes or not raw_bytes.strip():
        return _plain_error(400, "empty body")

    # Contract says ASCII only. Non-ASCII would invalidate any
    # downstream signature recompute, so reject hard instead of
    # silently replacing with `?`.
    try:
        raw = raw_bytes.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return _plain_error(400, "non-ascii bytes in body")

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

    # Length caps from the contract. Truncate silently would hide
    # client bugs; reject so the client's own INI and the server stay
    # in sync.
    if parsed.nickname and len(parsed.nickname) > MAX_NICKNAME_LEN:
        return _plain_error(
            400, f"nickname too long (max {MAX_NICKNAME_LEN})"
        )
    if parsed.notes and len(parsed.notes) > MAX_NOTES_LEN:
        return _plain_error(
            400, f"notes too long (max {MAX_NOTES_LEN})"
        )

    db = get_db()
    submission_id, insert_error = _insert_with_retry(db, parsed)
    if insert_error is not None:
        return insert_error

    body = f"{submission_id}\n{_submission_url(submission_id)}\n"
    return Response(body, mimetype="text/plain", status=200)


def _insert_with_retry(db, parsed):
    """INSERT the parsed row, regenerating submission_id on PK collision.
    Returns (submission_id, None) on success, (None, Response) on fatal
    error. run_signature collisions are a 409 (duplicate run);
    submission_id collisions just retry."""
    for _ in range(ID_RETRY_LIMIT):
        submission_id = _new_submission_id()
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
            return submission_id, None
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "run_signature" in msg:
                existing = db.execute(
                    "SELECT id FROM submissions WHERE run_signature = ?",
                    (parsed.run_signature,),
                ).fetchone()
                if existing:
                    return None, _plain_error(
                        409,
                        f"duplicate: already recorded as "
                        f"{_submission_url(existing['id'])}",
                    )
                return None, _plain_error(400, f"db integrity: {e}")
            if "submissions.id" in msg or "primary key" in msg:
                # 32-bit id collision. Loop and pick a new one.
                continue
            return None, _plain_error(400, f"db integrity: {e}")
    return None, _plain_error(
        500, "submission_id retry budget exhausted"
    )


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
