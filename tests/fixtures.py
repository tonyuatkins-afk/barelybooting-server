"""Shared test fixtures: a canonical CERBERUS.INI snippet built to
satisfy the submit endpoint's required fields, plus knobs for
constructing variants."""

from __future__ import annotations


def canonical_ini(
    signature: str = "a1b2c3d4",
    run_signature: str = "deadbeefcafef00d",
    version: str = "0.7.0-rc1",
    ini_format: int = 1,
    cpu_class: str | None = "486",
    cpu_detected: str | None = "486DX2-66 / AMD Am486DX2",
    nickname: str | None = None,
    notes: str | None = None,
) -> str:
    """Produce a minimal-but-valid CERBERUS.INI blob."""
    lines = [
        "[cerberus]",
        f"version={version}",
        "schema_version=1.0",
        "signature_schema=1",
        f"ini_format={ini_format}",
        "mode=quick",
        "runs=1",
        f"signature={signature}",
        "results=4",
        "",
        "[cpu]",
    ]
    if cpu_class is not None:
        lines.append(f"class={cpu_class}")
    if cpu_detected is not None:
        lines.append(f"detected={cpu_detected}")
    lines += [
        "",
        "[fpu]",
        "detected=integrated",
        "friendly=integrated (80487)",
        "",
        "[memory]",
        "conventional_kb=640",
        "extended_kb=63076",
        "",
        "[cache]",
        "present=yes",
        "",
        "[bus]",
        "class=vlb",
        "",
        "[video]",
        "adapter=vga",
        "chipset=S3 Trio64",
        "",
        "[audio]",
        "detected=Sound Blaster 16",
        "",
        "[bios]",
        "family=ami",
        "",
        "[bench]",
        "cpu.dhrystones=32131",
        "fpu.k_whetstones=2100",
        "memory.write_kbps=15384",
        "memory.read_kbps=16260",
        "memory.copy_kbps=7220",
        "",
        "[environment]",
        "emulator=none",
        "",
        "[upload]",
    ]
    if nickname is not None:
        lines.append(f"nickname={nickname}")
    if notes is not None:
        lines.append(f"notes={notes}")
    lines += [
        "",
        f"run_signature={run_signature}",
        "",
    ]
    return "\n".join(lines)
