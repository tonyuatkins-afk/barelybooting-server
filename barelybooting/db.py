"""SQLite connection + schema management.

The schema is deliberately flat: one ``submissions`` row per CERBERUS
run, with every commonly-queried INI field promoted to a column. The
raw INI text is kept too so we can re-parse if the extractor evolves.

``init_db()`` is idempotent — safe to call on every startup. New
columns added in future schema revisions should go in ``SCHEMA_V<N>``
and use ``_migrate()`` to apply.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id                  TEXT PRIMARY KEY,
    received_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Identity
    hardware_signature  TEXT NOT NULL,
    run_signature       TEXT UNIQUE NOT NULL,
    ini_raw             TEXT NOT NULL,
    ini_format          INTEGER NOT NULL,
    client_version      TEXT NOT NULL,

    -- Upload metadata
    nickname            TEXT,
    notes               TEXT,

    -- Extracted CPU fields
    cpu_class           TEXT,
    cpu_detected        TEXT,

    -- Extracted FPU fields
    fpu_detected        TEXT,

    -- Extracted memory fields
    memory_conv_kb      INTEGER,
    memory_ext_kb       INTEGER,

    -- Cache + bus
    cache_present       TEXT,
    bus_class           TEXT,

    -- Video
    video_adapter       TEXT,
    video_chipset       TEXT,

    -- Audio
    audio_detected      TEXT,

    -- BIOS
    bios_family         TEXT,

    -- Benchmarks
    dhrystones          INTEGER,
    whetstone_kwips     INTEGER,
    mem_write_kbps      INTEGER,
    mem_read_kbps       INTEGER,
    mem_copy_kbps       INTEGER,

    -- Environment
    emulator            TEXT
);

CREATE INDEX IF NOT EXISTS idx_submissions_hw_sig
    ON submissions(hardware_signature);

CREATE INDEX IF NOT EXISTS idx_submissions_received
    ON submissions(received_at DESC);

CREATE INDEX IF NOT EXISTS idx_submissions_cpu_class
    ON submissions(cpu_class);
"""


def get_db() -> sqlite3.Connection:
    """Per-request connection, cached on Flask's ``g`` proxy."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        # Better write perf without sacrificing durability for our load.
        g.db.execute("PRAGMA journal_mode = WAL;")
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def standalone_db(path: str | Path):
    """Connection outside the Flask request context — used by CLI
    commands (init-db, seed) and tests that don't spin up an app."""
    conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str | Path) -> None:
    """Create the schema. Idempotent: safe on re-run."""
    with standalone_db(path) as conn:
        conn.executescript(SCHEMA)


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
