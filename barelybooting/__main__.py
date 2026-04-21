"""CLI entry: python -m barelybooting <command>

Commands:
  init-db              Create the SQLite schema (idempotent).
  run                  Start the dev server on 127.0.0.1:5000.
  seed <dir>           POST every *.INI in <dir> to the running server.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import create_app
from .db import init_db


def cmd_init_db(args: argparse.Namespace) -> int:
    path = args.database or os.environ.get(
        "BAREBOOT_DB",
        os.path.join(os.getcwd(), "barelybooting.sqlite"),
    )
    init_db(path)
    print(f"initialized database at {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    app = create_app()
    # Ensure the DB exists before serving. Cheap and non-destructive.
    init_db(app.config["DATABASE"])
    host = args.host or "127.0.0.1"
    port = int(args.port or 5000)
    app.run(host=host, port=port, debug=args.debug)
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """POST every *.INI file under <dir> through the real submit
    endpoint. Uses urllib so no extra dependency. Skips files whose
    run_signature already exists (duplicate detection happens
    server-side — we just count the 409s)."""
    source = Path(args.dir)
    if not source.is_dir():
        print(f"error: {source} is not a directory", file=sys.stderr)
        return 2

    target = args.target.rstrip("/")
    # Case-insensitive suffix match. rglob("*.INI") + rglob("*.ini")
    # misses .Ini, .iNi etc.; match on suffix.lower() instead.
    inis = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() == ".ini"
    )
    if not inis:
        print(f"no .INI files found under {source}")
        return 0

    uploaded = duplicated = failed = 0
    for ini in inis:
        body = ini.read_bytes()
        req = urllib.request.Request(
            f"{target}/api/v1/submit",
            data=body,
            method="POST",
            headers={"Content-Type": "text/plain"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_text = resp.read().decode("ascii", "replace")
                first_line = resp_text.splitlines()[0] if resp_text else ""
                print(f"[ok]   {ini.name} -> {first_line}")
                uploaded += 1
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("ascii", "replace").strip()
            if e.code == 409:
                print(f"[dup]  {ini.name}: {err_body}")
                duplicated += 1
            else:
                print(f"[err]  {ini.name}: HTTP {e.code} {err_body}")
                failed += 1
        except urllib.error.URLError as e:
            print(f"[err]  {ini.name}: {e.reason}")
            failed += 1

    print()
    print(f"seed summary: {uploaded} uploaded, "
          f"{duplicated} duplicates (already present), "
          f"{failed} failed")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barelybooting")
    subs = parser.add_subparsers(dest="command", required=True)

    p_init = subs.add_parser("init-db", help="create SQLite schema")
    p_init.add_argument("--database", help="path to .sqlite file")
    p_init.set_defaults(func=cmd_init_db)

    p_run = subs.add_parser("run", help="start dev server")
    p_run.add_argument("--host")
    p_run.add_argument("--port", type=int)
    p_run.add_argument("--debug", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_seed = subs.add_parser("seed", help="bulk-POST .INI files from a dir")
    p_seed.add_argument("dir", help="directory containing *.INI files")
    p_seed.add_argument(
        "--target", default="http://127.0.0.1:5000",
        help="server base URL (default: http://127.0.0.1:5000)",
    )
    p_seed.set_defaults(func=cmd_seed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
