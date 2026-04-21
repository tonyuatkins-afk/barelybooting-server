"""barelybooting-server, CERBERUS results browser + upload intake.

Flask application factory. Routes are split across two blueprints:

* ``api``      machine-facing JSON/plain-text endpoints (submit, health)
* ``browse``   human-facing HTML pages (list, detail, per-CPU-class)

Configuration keys (all settable via env or ``create_app({...})``):

=====================  =============  ==========================================
Key                    Env var        Meaning
=====================  =============  ==========================================
DATABASE               BAREBOOT_DB    Path to the SQLite file.
PUBLIC_BASE            BAREBOOT_      External URL used when building per-
                       PUBLIC_BASE    submission links in the submit response.
BAREBOOT_ENV           BAREBOOT_ENV   ``production`` hardens startup checks.
MAX_CONTENT_LENGTH     (none)         64 KB by default; matches the upload
                                      contract's server commitment.
RATELIMIT_ENABLED      BAREBOOT_      Flask-Limiter master switch.
                       RATELIMIT      ``0`` disables (tests / dev).
RATELIMIT_SUBMIT       BAREBOOT_      Rate-limit spec for POST /api/v1/submit.
                       RATELIMIT_     Flask-Limiter string syntax.
                       SUBMIT
=====================  =============  ==========================================
"""

import os
import sqlite3

from flask import Flask, Response, request

from . import db
from .extensions import limiter
from .routes import api, browse


_LOCAL_PUBLIC_BASE = "http://127.0.0.1:5000"


def _security_headers(resp):
    """Belt-and-braces response headers. The site has no JS and no
    third-party assets; we lock both down explicitly so a future
    ``|safe`` slip cannot load a script or embed the page in a frame."""
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


def _log_client_errors(resp):
    """Leave a minimal audit trail for any 4xx/5xx. Logs the real client
    IP (CF-Connecting-IP behind the tunnel; remote_addr as fallback),
    the method, path, and a truncated User-Agent. Retention is whatever
    the docker/host log rotation policy says; these logs are expected
    to identify specific abusers, not to remain forever."""
    if resp.status_code >= 400 and request.path != "/api/v1/health":
        client_ip = (
            request.headers.get("CF-Connecting-IP")
            or request.remote_addr
            or "-"
        )
        ua = request.headers.get("User-Agent", "-")[:120]
        from flask import current_app
        current_app.logger.warning(
            "client_error status=%d method=%s path=%s ip=%s ua=%r",
            resp.status_code, request.method, request.path,
            client_ip, ua,
        )
    return resp


def _db_busy_handler(e):
    """OperationalError at the read path (browse routes, CLI-via-app)
    lands here. The submit route has its own targeted catch that returns
    a 503 with a friendlier body; this is the generic fallback."""
    from flask import current_app
    current_app.logger.warning("db busy at read path: %s", e)
    return Response(
        "error: database busy, retry shortly\n",
        status=503,
        mimetype="text/plain",
    )


def _validate_production_config(app: Flask) -> None:
    """In production mode, refuse to start if PUBLIC_BASE still points
    at the local fallback. A misconfigured deploy silently returning
    loopback URLs to DOS clients is exactly the kind of footgun we want
    to catch before the first POST."""
    if app.config.get("BAREBOOT_ENV") != "production":
        return
    base = app.config.get("PUBLIC_BASE", "")
    if not base or "127.0.0.1" in base or "localhost" in base:
        raise RuntimeError(
            "BAREBOOT_ENV=production requires BAREBOOT_PUBLIC_BASE to be "
            "set to a real external URL (e.g. https://barelybooting.com). "
            f"Got: {base!r}"
        )


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    app.config.from_mapping(
        DATABASE=os.environ.get(
            "BAREBOOT_DB",
            os.path.join(os.getcwd(), "barelybooting.sqlite"),
        ),
        PUBLIC_BASE=os.environ.get(
            "BAREBOOT_PUBLIC_BASE",
            _LOCAL_PUBLIC_BASE,
        ),
        BAREBOOT_ENV=os.environ.get("BAREBOOT_ENV", "development"),
        MAX_CONTENT_LENGTH=64 * 1024,
        RATELIMIT_ENABLED=os.environ.get(
            "BAREBOOT_RATELIMIT", "1"
        ) != "0",
        RATELIMIT_SUBMIT=os.environ.get(
            "BAREBOOT_RATELIMIT_SUBMIT", "30 per hour; 5 per minute"
        ),
    )
    if config_overrides:
        app.config.update(config_overrides)

    _validate_production_config(app)

    db.init_app(app)
    limiter.init_app(app)
    app.after_request(_security_headers)
    app.after_request(_log_client_errors)
    app.register_error_handler(sqlite3.OperationalError, _db_busy_handler)

    app.register_blueprint(api.bp)
    app.register_blueprint(browse.bp)

    return app
