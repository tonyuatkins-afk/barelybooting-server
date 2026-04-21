"""barelybooting-server, CERBERUS results browser + upload intake.

Flask application factory. Routes are split across two blueprints:

* ``api``      machine-facing JSON/plain-text endpoints (submit, health)
* ``browse``   human-facing HTML pages (list, detail, per-CPU-class)

Config is minimal: ``DATABASE`` is the SQLite file path, ``PUBLIC_BASE``
is the external URL used to build per-submission links in the
``POST /api/v1/submit`` response. Both have sensible defaults for local
dev.
"""

import os
from flask import Flask

from . import db
from .extensions import limiter
from .routes import api, browse


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


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    app.config.from_mapping(
        DATABASE=os.environ.get(
            "BAREBOOT_DB",
            os.path.join(os.getcwd(), "barelybooting.sqlite"),
        ),
        PUBLIC_BASE=os.environ.get(
            "BAREBOOT_PUBLIC_BASE",
            "http://127.0.0.1:5000",
        ),
        # Max upload body. CERBERUS INIs are typically 4 to 8 KB; 64 KB
        # matches the contract's server commitment.
        MAX_CONTENT_LENGTH=64 * 1024,
        # Flask-Limiter: let env disable it for tests / local dev.
        RATELIMIT_ENABLED=os.environ.get(
            "BAREBOOT_RATELIMIT", "1"
        ) != "0",
    )
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    limiter.init_app(app)
    app.after_request(_security_headers)

    app.register_blueprint(api.bp)
    app.register_blueprint(browse.bp)

    return app
