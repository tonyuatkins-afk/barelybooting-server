"""Shared Flask extension objects, instantiated at import time and
attached to the app inside ``create_app``. Lives in its own module so
blueprint code can import these without triggering the circular import
that a package-level ``__init__.py`` creates."""

from flask import request
from flask_limiter import Limiter


def _client_key() -> str:
    """Rate-limit key. Behind the Cloudflare Tunnel, request.remote_addr
    is always the sidecar's address and useless for per-client limiting.
    Cloudflare sets ``CF-Connecting-IP`` with the true origin IP on
    every request, which the tunnel preserves.

    We deliberately DO NOT honor ``X-Forwarded-For`` here: Cloudflare
    does not set that header in our topology, so any value we'd see in
    it came from a client who set it themselves. Trusting XFF would let
    any POSTer spoof the rate-limit key. CF-Connecting-IP is
    trustworthy because the only thing that can deliver requests to
    this container is the cloudflared sidecar on the internal-only
    Docker network (see docker-compose.yml: app_internal has
    internal: true)."""
    return (
        request.headers.get("CF-Connecting-IP")
        or request.remote_addr
        or "unknown"
    )


limiter = Limiter(
    key_func=_client_key,
    default_limits=[],
    storage_uri="memory://",
    headers_enabled=True,
)
