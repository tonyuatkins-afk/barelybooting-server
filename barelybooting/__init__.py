"""barelybooting-server — CERBERUS results browser + upload intake.

Flask application factory. Routes are split across two blueprints:

* ``api``      — machine-facing JSON/plain-text endpoints (submit, health)
* ``browse``   — human-facing HTML pages (list, detail, per-CPU-class)

Config is minimal: ``DATABASE`` is the SQLite file path, ``PUBLIC_BASE``
is the external URL used to build per-submission links in the
``POST /api/v1/submit`` response. Both have sensible defaults for local
dev.
"""

import os
from flask import Flask

from . import db
from .routes import api, browse


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    # Defaults — any of these can be overridden via env or config_overrides.
    app.config.from_mapping(
        DATABASE=os.environ.get(
            "BAREBOOT_DB",
            os.path.join(os.getcwd(), "barelybooting.sqlite"),
        ),
        PUBLIC_BASE=os.environ.get(
            "BAREBOOT_PUBLIC_BASE",
            "http://127.0.0.1:5000",
        ),
        # Max upload body — CERBERUS INIs are typically 4-8 KB. 64 KB
        # matches the contract's server commitment.
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)

    app.register_blueprint(api.bp)
    app.register_blueprint(browse.bp)

    return app
