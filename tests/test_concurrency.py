"""Concurrency assurance.

Waitress serves the app with 8 worker threads by default. SQLite's WAL
journal mode is supposed to handle that workload cleanly, but the claim
needs a test: no lost rows, no spurious 400s, no deadlocks, every POST
gets a distinct submission id.

Flask's test client is not thread-safe (the app context is stored in a
per-thread ContextVar that the test client's context manager does not
synchronize across threads), so we run a real waitress server in a
background thread and drive it with urllib from a ThreadPoolExecutor.
Close to the production topology; fails loud on the first bug.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from waitress.server import create_server

from barelybooting import create_app
from barelybooting.db import init_db

from .fixtures import canonical_ini


N_CONCURRENT = 50


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server():
    """Start a real waitress server in a background thread. Yields
    ``(base_url, db_path)``. Waitress runs single-threaded here (threads=4
    is enough for the test workload) and is torn down via server.close()
    after the test returns."""
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    init_db(db_path)
    app = create_app({
        "DATABASE": db_path,
        "TESTING": True,
        "RATELIMIT_ENABLED": False,
    })
    port = _free_port()
    server = create_server(app, host="127.0.0.1", port=port, threads=4)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the listener to accept connections before handing the
    # fixture to the test. 50ms is more than enough in practice.
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)

    yield f"http://127.0.0.1:{port}", db_path

    server.close()
    thread.join(timeout=5.0)
    os.unlink(db_path)


def _post_one(base_url: str, i: int) -> int:
    body = canonical_ini(
        # 16 hex chars: "cafe" prefix + 12-digit zero-padded index.
        run_signature=f"cafe{i:012d}",
    ).encode("ascii")
    req = urllib.request.Request(
        f"{base_url}/api/v1/submit",
        data=body,
        method="POST",
        headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_concurrent_submissions_all_persist(live_server):
    """50 concurrent POSTs must produce 50 rows. Zero 4xx, zero 5xx."""
    base_url, db_path = live_server
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [
            pool.submit(_post_one, base_url, i) for i in range(N_CONCURRENT)
        ]
        statuses = [f.result() for f in as_completed(futures)]

    assert all(s == 200 for s in statuses), statuses

    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    finally:
        conn.close()
    assert n == N_CONCURRENT


def _post_one_returning_id(base_url: str, i: int):
    body = canonical_ini(
        # 16 hex chars: "cafe" prefix + 12-digit zero-padded index.
        run_signature=f"cafe{i:012d}",
    ).encode("ascii")
    req = urllib.request.Request(
        f"{base_url}/api/v1/submit",
        data=body,
        method="POST",
        headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("ascii").splitlines()[0]


def test_concurrent_submissions_ids_unique(live_server):
    """Distinct run_signatures must produce distinct submission ids,
    even under the thread-scheduling noise of parallel inserts."""
    base_url, _ = live_server
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [
            pool.submit(_post_one_returning_id, base_url, i)
            for i in range(N_CONCURRENT)
        ]
        ids = [f.result() for f in as_completed(futures)]

    assert len(ids) == N_CONCURRENT
    assert len(set(ids)) == len(ids), "submission ids must be unique"
