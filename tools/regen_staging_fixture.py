"""Regenerate the DOSBox Staging fixture in one command.

Launches DOSBox Staging with the CERBERUS repo's
``devenv/smoketest-staging.conf``, waits for the run to complete,
copies the resulting INI into ``tests/fixtures/staging_dosbox.ini``,
and diffs against the previous version so any content drift shows
up in your shell before you commit.

Usage:
  python tools/regen_staging_fixture.py

Options:
  --cerberus PATH    Path to the CERBERUS repo (default: guess from this
                     tool's location, assuming the two repos are siblings)
  --dosbox PATH      DOSBox Staging dosbox.exe path (default: the winget
                     install location)
  --timeout SECONDS  Max seconds to wait for DOSBox to complete (60)
  --no-diff          Skip the diff against the old fixture

Exits nonzero if the DOSBox run didn't produce an INI, or the INI
fails the ini_format=1 + required-fields contract.

This is the "run it, commit the diff" loop:
  python tools/regen_staging_fixture.py && git diff tests/fixtures/
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_DOSBOX = Path(
    os.environ.get(
        "DOSBOX_STAGING",
        r"C:\Users\tonyu\AppData\Local\Programs\DOSBox Staging\dosbox.exe",
    )
)


def guess_cerberus_repo(this_file: Path) -> Path:
    """Walk up from this script's location, then try sibling 'CERBERUS'."""
    barelybooting = this_file.resolve().parent.parent.parent
    candidate = barelybooting / "CERBERUS"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        f"Cannot guess CERBERUS repo location; pass --cerberus explicitly. "
        f"Tried: {candidate}"
    )


def run_dosbox(
    dosbox: Path, conf: Path, timeout_s: int, marker: Path
) -> None:
    """Launch DOSBox Staging, poll for the marker file, kill if not done."""
    if not dosbox.is_file():
        raise FileNotFoundError(f"DOSBox Staging not found at {dosbox}")
    if not conf.is_file():
        raise FileNotFoundError(f"Smoketest config not found at {conf}")

    marker.unlink(missing_ok=True)

    proc = subprocess.Popen(
        [str(dosbox), "-conf", str(conf), "-exit"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(timeout_s):
            if marker.is_file():
                time.sleep(1)  # let the final flush settle
                return
            if proc.poll() is not None:
                return  # dosbox exited on its own
            time.sleep(1)
        raise TimeoutError(
            f"DOSBox Staging did not complete within {timeout_s}s "
            f"(marker {marker} not seen). Likely CERBERUS hang."
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def validate_ini(ini_bytes: bytes) -> None:
    """Cheap contract check: ini_format=1, required [cerberus] fields,
    run_signature present. Raises if anything's missing."""
    text = ini_bytes.decode("ascii", errors="strict")
    required = [
        "[cerberus]",
        "ini_format=1",
        "version=",
        "signature=",
        "run_signature=",
    ]
    missing = [r for r in required if r not in text]
    if missing:
        raise ValueError(
            f"Captured INI missing contract markers: {missing}"
        )


def show_diff(old_bytes: bytes, new_bytes: bytes, label: str) -> None:
    """Unified diff to stdout."""
    old = old_bytes.decode("ascii", errors="replace").splitlines(keepends=True)
    new = new_bytes.decode("ascii", errors="replace").splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old, new, fromfile=f"{label} (before)", tofile=f"{label} (after)",
        n=2,
    ))
    if diff:
        print("".join(diff), end="")
    else:
        print(f"{label}: unchanged")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cerberus", type=Path, default=None,
                    help="CERBERUS repo path (default: guess)")
    ap.add_argument("--dosbox", type=Path, default=DEFAULT_DOSBOX,
                    help=f"DOSBox Staging exe (default: {DEFAULT_DOSBOX})")
    ap.add_argument("--timeout", type=int, default=60,
                    help="Seconds to wait for DOSBox (default: 60)")
    ap.add_argument("--no-diff", action="store_true",
                    help="Skip the before/after diff")
    args = ap.parse_args(argv)

    here = Path(__file__).resolve()
    cerberus = args.cerberus or guess_cerberus_repo(here)
    conf = cerberus / "devenv" / "smoketest-staging.conf"
    ini_src = cerberus / "devenv" / "SMOKE-RC.INI"
    rc_marker = cerberus / "devenv" / "SMOKE-RC.RC"
    fixture = here.parent.parent / "tests" / "fixtures" / "staging_dosbox.ini"

    print(f"[info] cerberus repo: {cerberus}")
    print(f"[info] dosbox:        {args.dosbox}")
    print(f"[info] fixture:       {fixture}")
    print(f"[info] launching DOSBox Staging (timeout {args.timeout}s)")

    run_dosbox(args.dosbox, conf, args.timeout, rc_marker)

    if not ini_src.is_file():
        print(f"[err] capture file {ini_src} not produced", file=sys.stderr)
        return 1

    new_bytes = ini_src.read_bytes()
    validate_ini(new_bytes)
    print(f"[ok]   captured {ini_src.name} ({len(new_bytes)} bytes), "
          f"contract markers present")

    old_bytes = fixture.read_bytes() if fixture.is_file() else b""

    if not args.no_diff:
        print("--- diff ---")
        show_diff(old_bytes, new_bytes, fixture.name)
        print("--- /diff ---")

    fixture.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ini_src, fixture)
    print(f"[ok]   wrote {fixture}")
    print()
    print("Next steps:")
    print("  cd barelybooting-server")
    print("  git diff tests/fixtures/staging_dosbox.ini")
    print("  python -m pytest tests/test_staging_fixture.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
