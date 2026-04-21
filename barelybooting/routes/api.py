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

import re
import secrets
import sqlite3
from flask import Blueprint, Response, current_app, jsonify, request

from ..db import get_db
from ..extensions import limiter
from ..ini_parser import parse_ini_text


# Contract shapes. Hardware signature is an 8-char SHA-1 prefix; run
# signature is either the 16-char prefix (current emission) or the full
# 40-char digest (legacy / future). Anything else is junk and gets 400'd
# before it enters permanent storage where browse URLs would immortalize it.
_HW_SIG_RE = re.compile(r"^[0-9a-f]{8}$")
_RUN_SIG_RE = re.compile(r"^[0-9a-f]{16}$|^[0-9a-f]{40}$")


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


def _rate_limit_spec() -> str:
    """Read the submit endpoint's rate-limit spec from config at decoration
    time. Evaluated lazily via Flask-Limiter's callable support so operators
    can tune without a code edit."""
    return current_app.config.get(
        "RATELIMIT_SUBMIT", "30 per hour; 5 per minute"
    )


@bp.route("/health", methods=["GET"])
@limiter.exempt
def health():
    return jsonify({"status": "ok"})


@bp.route("/submit", methods=["POST"])
@limiter.limit(_rate_limit_spec)
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

    # Shape validation: identifiers MUST be hex. Normalization happened
    # in the parser; here we enforce the format itself so garbage like
    # "thisisnotasig" cannot immortalize itself as a browse URL.
    if not _HW_SIG_RE.match(parsed.hardware_signature):
        return _plain_error(400, "malformed hardware_signature (need 8 hex chars)")
    if not _RUN_SIG_RE.match(parsed.run_signature):
        return _plain_error(400, "malformed run_signature (need 16 or 40 hex chars)")

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


# Column order for the submissions INSERT. Derived once here so the SQL
# and the bind tuple cannot drift. Adding a column means one line here
# plus the matching attribute on ParsedIni.
_SUBMIT_COLUMNS = (
    "id",
    "hardware_signature",
    "run_signature",
    "ini_raw",
    "ini_format",
    "client_version",
    "nickname",
    "notes",
    "cpu_class",
    "cpu_detected",
    "fpu_detected",
    "memory_conv_kb",
    "memory_ext_kb",
    "cache_present",
    "bus_class",
    "video_adapter",
    "video_chipset",
    "audio_detected",
    "bios_family",
    "dhrystones",
    "whetstone_kwips",
    "mem_write_kbps",
    "mem_read_kbps",
    "mem_copy_kbps",
    "emulator",
)
# The ON CONFLICT clause makes run_signature duplicates silent
# (rowcount=0, no exception), so we can distinguish "duplicate run
# resubmitted" from "PK collision on id" via the insert's side-effect
# count instead of matching substrings in the SQLite error message. The
# id column's PK constraint is still enforced and still raises on
# collision, which keeps the retry loop working.
_SUBMIT_SQL = (
    f"INSERT INTO submissions ({', '.join(_SUBMIT_COLUMNS)}) "
    f"VALUES ({', '.join(['?'] * len(_SUBMIT_COLUMNS))}) "
    f"ON CONFLICT(run_signature) DO NOTHING"
)


def _insert_with_retry(db, parsed):
    """INSERT the parsed row, regenerating submission_id on PK collision.
    Returns (submission_id, None) on success, (None, Response) on fatal
    error. run_signature collisions land as ON CONFLICT DO NOTHING
    (rowcount=0, 409 returned); submission_id collisions still raise
    IntegrityError and trigger the retry loop."""
    for _ in range(ID_RETRY_LIMIT):
        submission_id = _new_submission_id()
        values = (submission_id,) + tuple(
            getattr(parsed, col) for col in _SUBMIT_COLUMNS[1:]
        )
        try:
            cur = db.execute(_SUBMIT_SQL, values)
            db.commit()
        except sqlite3.OperationalError as e:
            # "database is locked" or similar WAL-pressure condition.
            # get_db() sets busy_timeout so these should be rare, but
            # under a real flood we'd rather return a controlled 503
            # than a 500 stack trace.
            current_app.logger.warning("db busy on insert: %s", e)
            return None, _plain_error(503, "database busy, retry shortly")
        except sqlite3.IntegrityError as e:
            # With ON CONFLICT(run_signature) DO NOTHING, the only
            # remaining integrity path here is a PK collision on id.
            # Loop and pick a new id; no string-matching needed.
            current_app.logger.debug("submission_id PK collision: %s", e)
            continue

        if cur.rowcount == 1:
            return submission_id, None

        # rowcount == 0 means ON CONFLICT(run_signature) DO NOTHING
        # fired: this run_signature already exists. Fetch the existing
        # submission's id so the 409 body points at it.
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
        # Theoretical path: ON CONFLICT fired but the row vanished
        # between INSERT and SELECT. Treat as a controlled 409 rather
        # than pretending the insert succeeded.
        return None, _plain_error(409, "duplicate run_signature")

    return None, _plain_error(
        500, "submission_id retry budget exhausted"
    )


def _submission_url(sub_id: str) -> str:
    base = current_app.config["PUBLIC_BASE"].rstrip("/")
    return f"{base}/cerberus/run/{sub_id}"


def _plain_error(status: int, message: str) -> Response:
    return Response(
        f"error: {message}\n",
        status=status,
        mimetype="text/plain",
    )
