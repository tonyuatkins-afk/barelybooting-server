"""INI parser unit tests. Pure-function, no app/DB."""

from __future__ import annotations

from barelybooting.ini_parser import parse_ini_text

from .fixtures import canonical_ini


def test_parses_canonical_ini():
    p = parse_ini_text(canonical_ini())
    assert p.ini_format == 1
    assert p.hardware_signature == "a1b2c3d4"
    assert p.run_signature == "deadbeefcafef00d"
    assert p.client_version == "0.7.0-rc1"
    assert p.cpu_class == "486"
    assert p.cpu_detected == "486DX2-66 / AMD Am486DX2"
    assert p.memory_conv_kb == 640
    assert p.memory_ext_kb == 63076
    assert p.dhrystones == 32131
    assert p.whetstone_kwips == 2100
    assert p.emulator == "none"


def test_empty_ini_returns_none_fields():
    p = parse_ini_text("")
    assert p.ini_format is None
    assert p.hardware_signature is None
    assert p.run_signature is None
    assert p.cpu_class is None


def test_ignores_unknown_sections_and_keys():
    # Additive compatibility commitment from the contract: unknown
    # sections/keys must not break the parser.
    ini = canonical_ini() + "\n[future_section]\nsome_key=some_value\n"
    p = parse_ini_text(ini)
    assert p.ini_format == 1
    assert "future_section" in p.sections
    assert p.sections["future_section"]["some_key"] == "some_value"


def test_upload_empty_nickname_becomes_none():
    ini = canonical_ini(nickname="")
    p = parse_ini_text(ini)
    assert p.nickname is None


def test_malformed_integer_returns_none():
    ini = canonical_ini().replace(
        "memory.write_kbps=15384", "memory.write_kbps=not_a_number"
    )
    p = parse_ini_text(ini)
    assert p.mem_write_kbps is None
    # Other fields should still parse.
    assert p.mem_read_kbps == 16260
