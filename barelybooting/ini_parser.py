"""CERBERUS.INI parser.

Consumes the contract from the CERBERUS repo's ``docs/ini-format.md``.
Returns a flat dict of extracted fields plus the raw INI text preserved
verbatim. The parser is deliberately permissive: unknown sections and
keys are ignored (forward-compatible with additive INI changes per the
``ini_format=1`` guarantee).

Key design rule: never raise on malformed input. A missing section or
malformed value returns ``None`` for that field so the database row
has NULLs rather than rejected submissions. The server's own sanity
checks (required ``[cerberus]`` section with ``run_signature``) live
in the submit route, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")
KV_RE = re.compile(r"^([A-Za-z0-9_.]+)\s*=\s*(.*)$")


@dataclass
class ParsedIni:
    """Flat bag of extracted fields.

    Any attribute that was absent in the INI is left as ``None``. The
    raw INI text is preserved so the DB can archive the original bytes.
    """

    ini_raw: str
    sections: dict[str, dict[str, str]] = field(default_factory=dict)

    # Identity (from [cerberus])
    ini_format: Optional[int] = None
    client_version: Optional[str] = None
    hardware_signature: Optional[str] = None
    run_signature: Optional[str] = None

    # Upload metadata (from [upload])
    nickname: Optional[str] = None
    notes: Optional[str] = None

    # CPU
    cpu_class: Optional[str] = None
    cpu_detected: Optional[str] = None

    # FPU
    fpu_detected: Optional[str] = None

    # Memory
    memory_conv_kb: Optional[int] = None
    memory_ext_kb: Optional[int] = None

    # Cache / bus
    cache_present: Optional[str] = None
    bus_class: Optional[str] = None

    # Video
    video_adapter: Optional[str] = None
    video_chipset: Optional[str] = None

    # Audio
    audio_detected: Optional[str] = None

    # BIOS
    bios_family: Optional[str] = None

    # Benchmarks (all non-time metrics)
    dhrystones: Optional[int] = None
    whetstone_kwips: Optional[int] = None
    mem_write_kbps: Optional[int] = None
    mem_read_kbps: Optional[int] = None
    mem_copy_kbps: Optional[int] = None

    # Environment
    emulator: Optional[str] = None


def _as_int(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        return None


def parse_ini_text(text: str) -> ParsedIni:
    """Parse a CERBERUS.INI blob into a ``ParsedIni``. Robust to missing
    sections and unknown keys. Never raises on malformed content."""
    # --- 1. Tokenize into {section: {key: value}} -----------------------
    sections: dict[str, dict[str, str]] = {}
    current_section: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue

        # The trailing `run_signature=<hex>` line is written outside
        # any section; treat it as if it were in a pseudo-section.
        section_match = SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1).strip().lower()
            sections.setdefault(current_section, {})
            continue

        kv_match = KV_RE.match(line)
        if kv_match:
            key, value = kv_match.group(1).strip(), kv_match.group(2).strip()
            if current_section is None:
                # Outside-section KVs (like `run_signature=...` trailer)
                # land in a synthetic "_root" bucket so lookups still work.
                sections.setdefault("_root", {})[key.lower()] = value
            else:
                sections[current_section][key.lower()] = value

    # --- 2. Extract the fields the schema cares about -------------------
    result = ParsedIni(ini_raw=text, sections=sections)

    cerberus = sections.get("cerberus", {})
    result.ini_format = _as_int(cerberus.get("ini_format"))
    result.client_version = cerberus.get("version")
    result.hardware_signature = cerberus.get("signature")

    # run_signature is written as a bare trailer in CERBERUS.INI — no
    # section break precedes it, so depending on emit order it may land
    # in [_root] (if isolated), [upload] (current v0.7.0 layout), or
    # [consistency] (older layouts). Search every section for the key;
    # first hit wins. This is forward-compatible: a future CERBERUS
    # that promotes run_signature into [cerberus] explicitly still works.
    for sect_name, sect_kv in sections.items():
        if "run_signature" in sect_kv:
            result.run_signature = sect_kv["run_signature"]
            break

    upload = sections.get("upload", {})
    # Empty strings → None so the DB stores NULL instead of "".
    result.nickname = upload.get("nickname") or None
    result.notes = upload.get("notes") or None

    cpu = sections.get("cpu", {})
    result.cpu_class = cpu.get("class")
    result.cpu_detected = cpu.get("detected")

    fpu = sections.get("fpu", {})
    result.fpu_detected = fpu.get("detected") or fpu.get("friendly")

    memory = sections.get("memory", {})
    result.memory_conv_kb = _as_int(memory.get("conventional_kb"))
    result.memory_ext_kb = _as_int(memory.get("extended_kb"))

    cache = sections.get("cache", {})
    result.cache_present = cache.get("present")

    bus = sections.get("bus", {})
    result.bus_class = bus.get("class")

    video = sections.get("video", {})
    result.video_adapter = video.get("adapter")
    result.video_chipset = video.get("chipset")

    audio = sections.get("audio", {})
    result.audio_detected = audio.get("detected")

    bios = sections.get("bios", {})
    result.bios_family = bios.get("family")

    bench = sections.get("bench", {})
    result.dhrystones = _as_int(bench.get("cpu.dhrystones"))
    result.whetstone_kwips = _as_int(bench.get("fpu.k_whetstones"))
    result.mem_write_kbps = _as_int(bench.get("memory.write_kbps"))
    result.mem_read_kbps = _as_int(bench.get("memory.read_kbps"))
    result.mem_copy_kbps = _as_int(bench.get("memory.copy_kbps"))

    env = sections.get("environment", {})
    result.emulator = env.get("emulator")

    return result
