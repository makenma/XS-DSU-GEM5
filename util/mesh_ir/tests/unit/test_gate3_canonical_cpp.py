import math
import random
import struct
import subprocess
from pathlib import Path

import pytest

from mesh_ir.canonical import JSON_SAFE_INTEGER_MAX, canonical_json_bytes


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def canonical_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_canonical")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <charconv>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <sstream>
#include <streambuf>
#include <string>
#include <string_view>
#include <vector>

#include "dev/ai_mesh/mesh_canonical.hh"

using namespace gem5::ai_mesh::mesh_canonical;

uint8_t
nibble(char value)
{
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;
    return value - 'A' + 10;
}

bool
decode(std::string_view value, std::vector<uint8_t> &result)
{
    if (value == "-")
        return true;
    if (value.size() % 2 != 0)
        return false;
    for (size_t index = 0; index < value.size(); index += 2) {
        result.push_back(
            (nibble(value[index]) << 4) | nibble(value[index + 1]));
    }
    return true;
}

void
emit(bool result, const std::ostringstream &output)
{
    const auto value = output.str();
    std::cout << (result ? '1' : '0') << '\t' << value.size() << '\t'
              << value << '\n';
}

class RejectBuffer : public std::streambuf
{
  protected:
    std::streamsize
    xsputn(const char *, std::streamsize) override
    {
        return 0;
    }

    int_type
    overflow(int_type) override
    {
        return traits_type::eof();
    }
};

template<class Writer>
bool
rejected(Writer writer)
{
    RejectBuffer buffer;
    std::ostream output(&buffer);
    return !writer(output);
}

int
main()
{
    char kind;
    std::string encoded;
    while (std::cin >> kind >> encoded) {
        std::vector<uint8_t> bytes;
        if ((kind == 's' || kind == 'v' || kind == 'y') &&
            !decode(encoded, bytes)) {
            return 7;
        }
        std::ostringstream output;
        if (kind == 's') {
            const std::string value(bytes.begin(), bytes.end());
            emit(writeString(output, value), output);
        } else if (kind == 'v') {
            const std::string value(bytes.begin(), bytes.end());
            output << (validUtf8(value) ? "true" : "false");
            emit(true, output);
        } else if (kind == 'u') {
            uint64_t value = 0;
            const auto result = std::from_chars(
                encoded.data(), encoded.data() + encoded.size(), value, 16);
            if (result.ec != std::errc{})
                return 2;
            emit(writeUnsigned(output, value), output);
        } else if (kind == 'i') {
            uint64_t bits = 0;
            const auto result = std::from_chars(
                encoded.data(), encoded.data() + encoded.size(), bits, 16);
            if (result.ec != std::errc{})
                return 3;
            int64_t value;
            std::memcpy(&value, &bits, sizeof(value));
            emit(writeSigned(output, value), output);
        } else if (kind == 'f') {
            uint64_t bits = 0;
            const auto result = std::from_chars(
                encoded.data(), encoded.data() + encoded.size(), bits, 16);
            if (result.ec != std::errc{})
                return 4;
            double value;
            std::memcpy(&value, &bits, sizeof(value));
            emit(writeFloat(output, value), output);
        } else if (kind == 'b') {
            emit(writeBoolean(output, encoded == "1"), output);
        } else if (kind == 'y') {
            emit(writeBytes(output, bytes.data(), bytes.size()), output);
        } else if (kind == 'e') {
            const uint8_t byte = 0xab;
            std::cout
                << rejected([](std::ostream &out) {
                       return writeString(out, "value");
                   })
                << rejected([](std::ostream &out) {
                       return writeUnsigned(out, 7);
                   })
                << rejected([](std::ostream &out) {
                       return writeSigned(out, -7);
                   })
                << rejected([](std::ostream &out) {
                       return writeFloat(out, 1.25);
                   })
                << rejected([](std::ostream &out) {
                       return writeBoolean(out, true);
                   })
                << rejected([&byte](std::ostream &out) {
                       return writeBytes(out, &byte, 1);
                   })
                << '\n';
        } else {
            return 5;
        }
    }
    return std::cin.eof() ? 0 : 6;
}
'''.lstrip(),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic-errors",
            "-I",
            str(ROOT / "src"),
            str(source),
            str(ROOT / "src/dev/ai_mesh/mesh_canonical.cc"),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


def _run(probe: Path, rows: list[tuple[str, str]]) -> list[tuple[bool, str]]:
    payload = "".join(f"{kind} {value}\n" for kind, value in rows)
    result = subprocess.run(
        [str(probe)],
        input=payload,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split("\n")
    assert lines.pop() == ""
    parsed = []
    for line in lines:
        success, size, value = line.split("\t", 2)
        assert len(value.encode("utf-8")) == int(size)
        parsed.append((success == "1", value))
    assert len(parsed) == len(rows)
    return parsed


def _u64_bits(value: int) -> str:
    return f"{value & ((1 << 64) - 1):016x}"


def _float_bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def test_integer_boolean_and_bytes_match_python_canonical_scalars(
    canonical_probe,
):
    unsigned = [
        0,
        1,
        JSON_SAFE_INTEGER_MAX,
        JSON_SAFE_INTEGER_MAX + 1,
        (1 << 63) - 1,
        1 << 63,
        (1 << 64) - 1,
    ]
    signed = [
        -(1 << 63),
        -JSON_SAFE_INTEGER_MAX - 1,
        -JSON_SAFE_INTEGER_MAX,
        -1,
        0,
        1,
        JSON_SAFE_INTEGER_MAX,
        JSON_SAFE_INTEGER_MAX + 1,
        (1 << 63) - 1,
    ]
    byte_values = [b"", b"\x00", bytes(range(32)), b"\x00\x7f\x80\xff"]
    rows = (
        [("u", _u64_bits(value)) for value in unsigned]
        + [("i", _u64_bits(value)) for value in signed]
        + [("b", str(int(value))) for value in (False, True)]
        + [("y", value.hex() or "-") for value in byte_values]
    )
    expected = [
        canonical_json_bytes(value).decode("utf-8")
        for value in (*unsigned, *signed, False, True, *byte_values)
    ]
    assert _run(canonical_probe, rows) == [(True, value) for value in expected]


def test_strings_match_python_and_reject_invalid_utf8(canonical_probe):
    valid = [
        b"",
        b'quote"slash\\solidus/',
        bytes(range(32)),
        "¢€😀\u2028".encode("utf-8"),
        b"\xc2\x80\xdf\xbf",
        b"\xe0\xa0\x80\xef\xbf\xbf",
        b"\xf0\x90\x80\x80\xf4\x8f\xbf\xbf",
    ]
    invalid = [
        b"\x80",
        b"\xbf",
        b"\xc0\x80",
        b"\xc1\xbf",
        b"\xc2",
        b"\xc2\x20",
        b"\xe0\x80\x80",
        b"\xe1\x80",
        b"\xe1\x80\x20",
        b"\xed\xa0\x80",
        b"\xed\xbf\xbf",
        b"\xf0\x80\x80\x80",
        b"\xf1\x80\x80",
        b"\xf1\x80\x80\x20",
        b"\xf4\x90\x80\x80",
        b"\xf5\x80\x80\x80",
        b"\xff",
    ]
    values = valid + invalid
    encoded = [value.hex() or "-" for value in values]
    validity = _run(canonical_probe, [("v", value) for value in encoded])
    assert validity == [
        (True, "true") for _ in valid
    ] + [
        (True, "false") for _ in invalid
    ]
    written = _run(canonical_probe, [("s", value) for value in encoded])
    expected = [
        (True, canonical_json_bytes(value.decode("utf-8")).decode("utf-8"))
        for value in valid
    ]
    assert written == expected + [(False, "") for _ in invalid]


def test_floats_match_python_for_edges_transitions_and_random_bits(
    canonical_probe,
):
    bits = {
        0,
        1,
        0x000FFFFFFFFFFFFF,
        0x0010000000000000,
        0x7FEFFFFFFFFFFFFF,
        0x8000000000000000,
        0x8000000000000001,
        0xFFEFFFFFFFFFFFFF,
    }
    for center in (1e-4, 1e16, -1e-4, -1e16):
        bits.update(
            _float_bits(value)
            for value in (
                math.nextafter(center, -math.inf),
                center,
                math.nextafter(center, math.inf),
            )
        )
    generator = random.Random(77031337)
    while len(bits) < 4096:
        candidate = generator.getrandbits(64)
        if candidate & 0x7FF0000000000000 != 0x7FF0000000000000:
            bits.add(candidate)
    ordered = sorted(bits)
    values = [
        struct.unpack("<d", struct.pack("<Q", value))[0]
        for value in ordered
    ]
    actual = _run(
        canonical_probe,
        [("f", f"{value:016x}") for value in ordered],
    )
    expected = [
        canonical_json_bytes(value).decode("utf-8")
        for value in values
    ]
    assert actual == [(True, value) for value in expected]


def test_nonfinite_float_rejects_before_writing(canonical_probe):
    bits = [
        0x7FF0000000000000,
        0xFFF0000000000000,
        0x7FF8000000000000,
        0x7FF0000000000001,
    ]
    assert _run(
        canonical_probe,
        [("f", f"{value:016x}") for value in bits],
    ) == [(False, "") for _ in bits]


def test_all_writers_report_real_stream_failure(canonical_probe):
    result = subprocess.run(
        [str(canonical_probe)],
        input="e -\n",
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "111111\n"
