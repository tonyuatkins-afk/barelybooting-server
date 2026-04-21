"""Shared Flask extension objects, instantiated at import time and
attached to the app inside ``create_app``. Lives in its own module so
blueprint code can import these without triggering the circular import
that a package-level ``__init__.py`` creates."""

from flask import request
from flask_limiter import Limiter


def _client_key() -> str:
    """Rate-limit key. Behind the Cloudflare Tunnel, request.remote_addr
    is always the sidecar's address and useless for per-client limiting.
    Cloudflare sets ``CF-Connecting-IP`` with the true origin. The tunnel
    is the only ingress path, so trusting this header is safe: nothing
    else can reach the container."""
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or "unknown"
    )


limiter = Limiter(
    key_func=_client_key,
    default_limits=[],
    storage_uri="memory://",
    headers_enabled=True,
)
