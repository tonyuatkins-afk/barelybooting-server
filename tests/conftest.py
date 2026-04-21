"""Shared pytest fixtures. Every test file picks these up automatically.

The app fixture produces a fresh SQLite file per test and disables the
rate limiter. Disabling matters: Flask-Limiter's in-memory backend is
conservative about per-client counters, and a test that makes several
requests from the same ``127.0.0.1`` key (which the test client always
presents) would eventually exceed the production caps.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from barelybooting import create_app
from barelybooting.db import init_db


@pytest.fixture
def app():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    init_db(path)
    app = create_app({
        "DATABASE": path,
        "TESTING": True,
        "RATELIMIT_ENABLED": False,
    })
    yield app
    os.unlink(path)


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c
