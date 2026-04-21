"""End-to-end upload simulator.

Simulates what CERBERUS's HTGET shell-out does, but from Python, so we
can validate the server's intake path without a DOS environment. Useful
for:

  1. Smoke-testing the server before deploy.
  2. Feeding a real CERBERUS.INI (captured from a DOSBox-X smoketest
     or from the BEK-V409 bench) into the actual intake endpoint and
     confirming the full round-trip renders.
  3. Catching contract drift between the CERBERUS client's emission
     and the server's parser.

What's simulated:
  - HTTP POST to /api/v1/submit with ``Content-Type: text/plain``
  - Raw INI body, byte-for-byte (no re-encoding, no pretty-printing)
  - Parsing the two-line response (submission id + public URL)
  - GET against the returned /cerberus/run/<id> URL
  - Assertions on the rendered page

What's NOT simulated:
  - HTGET's exact command-line flag syntax
  - CERBERUS's UPLOAD.TMP response-parsing loop
  These are the only two paths the real-hardware 486 test exercises
  that this simulator can't. Covers ~80% of contract risk; the
  remaining ~20% lives in the DOS client shell-out and response
  parse, which the DOSBox-X smoketest + 486 validation still need.

Usage:
  # Start the server in another terminal:
  python -m barelybooting run

  # Default: uses the canonical test INI fixture
  python tools/simulate_upload.py

  # Point at a real captured INI:
  python tools/simulate_upload.py path/to/CERBERUS.INI

  # Point at a non-default server:
  python tools/simulate_upload.py --server http://127.0.0.1:8080 \\
      path/to/CERBERUS.INI

Exit codes:
  0  full round-trip passed
  1  POST failed (network, HTTP non-2xx)
  2  response parse failed (not two lines, bad URL)
  3  GET on the returned URL failed
  4  INI file not found
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_SERVER = "http://127.0.0.1:5000"


# Inlined minimal canonical INI so the script stays standalone (no
# dependency on the test package). Matches the same schema the parser
# exercises in tests/fixtures.py. Hex signatures are contract-valid.
CANONICAL_INI = """[cerberus]
version=0.7.0-rc2
schema_version=1.0
signature_schema=1
ini_format=1
mode=quick
runs=1
signature=51117a70
results=6

[network]
transport=none

[environment]
emulator=none
virtualized=no
confidence_penalty=none

[cpu]
class=486
detected=486DX2-66 / simulated bench
family_model_stepping=4.3.5

[fpu]
detected=integrated
friendly=integrated (80487)

[memory]
conventional_kb=640
extended_kb=63076

[cache]
present=yes

[bus]
class=vlb

[video]
adapter=vga
chipset=S3 Trio64

[audio]
detected=Sound Blaster 16

[bios]
family=ami
date=11/11/92

[bench]
cpu.dhrystones=32131
fpu.k_whetstones=2100
memory.write_kbps=15384
memory.read_kbps=16260
memory.copy_kbps=7220

[environment]
emulator=none

[upload]
nickname=simulator
notes=end-to-end round-trip test

run_signature=51117a7051117a70
"""


def log(level: str, msg: str) -> None:
    colour = {"ok": "\033[32m", "err": "\033[31m", "info": "\033[36m"}.get(
        level, ""
    )
    reset = "\033[0m" if colour else ""
    print(f"{colour}[{level:4s}]{reset} {msg}", flush=True)


def post_ini(server: str, body: bytes) -> tuple[int, bytes, dict]:
    """POST the raw INI body exactly as HTGET would. Returns
    (status_code, body_bytes, headers_dict)."""
    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/v1/submit",
        data=body,
        method="POST",
        headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except urllib.error.URLError as e:
        log("err", f"network error: {e.reason}")
        log("err", f"is the server running at {server} ?")
        sys.exit(1)


def parse_submit_response(body: bytes) -> tuple[str, str] | None:
    """Per contract: two lines, line 1 = 8-char hex id, line 2 = URL."""
    text = body.decode("ascii", errors="replace").strip()
    lines = text.splitlines()
    if len(lines) != 2:
        log("err", f"expected 2 lines, got {len(lines)}: {text!r}")
        return None
    sub_id, url = lines[0].strip(), lines[1].strip()
    if len(sub_id) != 8 or not all(c in "0123456789abcdef" for c in sub_id):
        log("err", f"submission id not 8 hex chars: {sub_id!r}")
        return None
    if not url.startswith(("http://", "https://")):
        log("err", f"url not absolute: {url!r}")
        return None
    return sub_id, url


def get_detail_page(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def run(server: str, ini_path: str | None) -> int:
    # --- Load INI body --------------------------------------------------
    if ini_path is None:
        log("info", "no INI path given; using built-in canonical fixture")
        body = CANONICAL_INI.encode("ascii")
        source_label = "<built-in canonical>"
    else:
        p = Path(ini_path)
        if not p.is_file():
            log("err", f"INI file not found: {p}")
            return 4
        body = p.read_bytes()
        source_label = str(p)

    log("info", f"source: {source_label} ({len(body)} bytes)")
    log("info", f"server: {server}")

    # --- Phase 1: POST to /api/v1/submit --------------------------------
    status, resp_body, headers = post_ini(server, body)
    log("info", f"POST /api/v1/submit -> {status}")

    if status == 400:
        # Expected for pre-v0.7.0 INIs that lack ini_format=1. Report
        # the server's message plainly; not all 400s are failures.
        msg = resp_body.decode("ascii", errors="replace").strip()
        log("info", f"server said: {msg}")
        log("err", "submission rejected (400). This is correct behavior")
        log("err", "if the INI lacks ini_format=1 or other required fields.")
        return 1
    if status == 413:
        log("err", "body too large (413). MAX_CONTENT_LENGTH is 64 KB.")
        return 1
    if status == 429:
        log("err", "rate limited (429). Slow down or disable the limiter.")
        return 1
    if status != 200:
        log("err", f"unexpected status {status}: {resp_body[:200]!r}")
        return 1

    parsed = parse_submit_response(resp_body)
    if parsed is None:
        return 2
    sub_id, detail_url = parsed
    log("ok", f"submission id: {sub_id}")
    log("ok", f"detail URL:    {detail_url}")

    # Useful security-header spot check: confirm CSP + XFO made it out.
    csp = headers.get("Content-Security-Policy", "")
    xfo = headers.get("X-Frame-Options", "")
    if "script-src 'none'" not in csp:
        log("err", "CSP missing script-src 'none'")
        return 2
    if xfo.upper() != "DENY":
        log("err", f"X-Frame-Options not DENY: {xfo!r}")
        return 2
    log("ok", "response headers include CSP + X-Frame-Options: DENY")

    # --- Phase 2: GET the detail page -----------------------------------
    detail_status, detail_body = get_detail_page(detail_url)
    log("info", f"GET {detail_url} -> {detail_status}")
    if detail_status != 200:
        log("err", f"detail page returned {detail_status}")
        return 3

    detail_text = detail_body.decode("utf-8", errors="replace")
    must_contain = [sub_id, "Submission", "Raw CERBERUS.INI"]
    missing = [m for m in must_contain if m not in detail_text]
    if missing:
        log("err", f"detail page missing expected content: {missing}")
        return 3
    log("ok", "detail page renders and contains the submission id + raw INI")

    # --- Phase 3: Duplicate handling ------------------------------------
    log("info", "re-POSTing the same body to verify dup detection")
    status2, resp2, _ = post_ini(server, body)
    if status2 == 409:
        log("ok", f"duplicate correctly rejected (409): "
            f"{resp2.decode('ascii', 'replace').strip()!r}")
    elif status2 == 200:
        log("err", "duplicate POST unexpectedly accepted as fresh (200).")
        log("err", "run_signature UNIQUE constraint may be broken.")
        return 3
    else:
        log("err", f"duplicate POST returned unexpected {status2}")
        return 3

    log("ok", "end-to-end round trip passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Simulate the CERBERUS upload transaction end to end."
    )
    p.add_argument(
        "ini", nargs="?", default=None,
        help="Path to a CERBERUS.INI file (default: built-in canonical fixture)",
    )
    p.add_argument(
        "--server", default=DEFAULT_SERVER,
        help=f"Server base URL (default: {DEFAULT_SERVER})",
    )
    args = p.parse_args(argv)
    return run(args.server, args.ini)


if __name__ == "__main__":
    sys.exit(main())
