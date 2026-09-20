#include "dev/ai_mesh/mesh_canonical.hh"

#include <array>
#include <charconv>
#include <cmath>
#include <limits>
#include <ostream>
#include <string>
#include <system_error>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_canonical
{

namespace
{

bool
writeRaw(std::ostream &out, std::string_view value)
{
    if (value.size() >
        static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) {
        return false;
    }
    out.write(value.data(), static_cast<std::streamsize>(value.size()));
    return static_cast<bool>(out);
}

}

static_assert(sizeof(double) == sizeof(uint64_t));
static_assert(std::numeric_limits<double>::is_iec559);

bool
validUtf8(std::string_view value)
{
    size_t index = 0;
    while (index < value.size()) {
        const auto lead = static_cast<uint8_t>(value[index]);
        if (lead < 0x80) {
            ++index;
            continue;
        }
        size_t continuationCount;
        uint8_t firstLow;
        uint8_t firstHigh;
        if (lead >= 0xc2 && lead <= 0xdf) {
            continuationCount = 1;
            firstLow = 0x80;
            firstHigh = 0xbf;
        } else if (lead == 0xe0) {
            continuationCount = 2;
            firstLow = 0xa0;
            firstHigh = 0xbf;
        } else if ((lead >= 0xe1 && lead <= 0xec) ||
                   lead == 0xee || lead == 0xef) {
            continuationCount = 2;
            firstLow = 0x80;
            firstHigh = 0xbf;
        } else if (lead == 0xed) {
            continuationCount = 2;
            firstLow = 0x80;
            firstHigh = 0x9f;
        } else if (lead == 0xf0) {
            continuationCount = 3;
            firstLow = 0x90;
            firstHigh = 0xbf;
        } else if (lead >= 0xf1 && lead <= 0xf3) {
            continuationCount = 3;
            firstLow = 0x80;
            firstHigh = 0xbf;
        } else if (lead == 0xf4) {
            continuationCount = 3;
            firstLow = 0x80;
            firstHigh = 0x8f;
        } else {
            return false;
        }
        if (continuationCount > value.size() - index - 1)
            return false;
        const auto first = static_cast<uint8_t>(value[index + 1]);
        if (first < firstLow || first > firstHigh)
            return false;
        for (size_t offset = 2; offset <= continuationCount; ++offset) {
            const auto continuation =
                static_cast<uint8_t>(value[index + offset]);
            if (continuation < 0x80 || continuation > 0xbf)
                return false;
        }
        index += continuationCount + 1;
    }
    return true;
}

bool
writeString(std::ostream &out, std::string_view value)
{
    if (!validUtf8(value))
        return false;
    if (!out.put('"'))
        return false;
    constexpr char hex[] = "0123456789abcdef";
    for (const char item : value) {
        const auto byte = static_cast<uint8_t>(item);
        std::string_view escaped;
        switch (byte) {
          case '"': escaped = "\\\""; break;
          case '\\': escaped = "\\\\"; break;
          case '\b': escaped = "\\b"; break;
          case '\f': escaped = "\\f"; break;
          case '\n': escaped = "\\n"; break;
          case '\r': escaped = "\\r"; break;
          case '\t': escaped = "\\t"; break;
          default:
            if (byte < 0x20) {
                const std::array<char, 6> unicodeEscape = {
                    '\\', 'u', '0', '0', hex[byte >> 4], hex[byte & 0xf]};
                if (!writeRaw(out, std::string_view(
                                       unicodeEscape.data(),
                                       unicodeEscape.size()))) {
                    return false;
                }
            } else if (!out.put(item)) {
                return false;
            }
            continue;
        }
        if (!writeRaw(out, escaped))
            return false;
    }
    return static_cast<bool>(out.put('"'));
}

bool
writeUnsigned(std::ostream &out, uint64_t value)
{
    std::array<char, 32> buffer{};
    auto *begin = buffer.data();
    auto *next = begin;
    const bool quoted = value > mesh_abi::kJsonSafeIntegerMax;
    if (quoted) {
        *next++ = '"';
        *next++ = '0';
        *next++ = 'x';
    }
    const auto converted = std::to_chars(
        next,
        buffer.data() + buffer.size() - (quoted ? 1 : 0),
        value,
        quoted ? 16 : 10);
    if (converted.ec != std::errc{})
        return false;
    next = converted.ptr;
    if (quoted)
        *next++ = '"';
    return writeRaw(out, std::string_view(begin, next - begin));
}

bool
writeSigned(std::ostream &out, int64_t value)
{
    if (value >= 0)
        return writeUnsigned(out, static_cast<uint64_t>(value));
    std::array<char, 32> buffer{};
    const auto converted = std::to_chars(
        buffer.data(), buffer.data() + buffer.size(), value, 10);
    if (converted.ec != std::errc{})
        return false;
    return writeRaw(
        out,
        std::string_view(buffer.data(), converted.ptr - buffer.data()));
}

bool
writeFloat(std::ostream &out, double value)
{
    if (!std::isfinite(value))
        return false;
    std::array<char, 64> buffer{};
    const auto converted = std::to_chars(
        buffer.data(),
        buffer.data() + buffer.size(),
        value,
        std::chars_format::scientific);
    if (converted.ec != std::errc{})
        return false;
    const std::string_view scientific(
        buffer.data(), converted.ptr - buffer.data());
    const auto exponentOffset = scientific.find('e');
    if (exponentOffset == std::string_view::npos ||
        exponentOffset + 2 >= scientific.size()) {
        return false;
    }
    const char exponentSign = scientific[exponentOffset + 1];
    if (exponentSign != '+' && exponentSign != '-')
        return false;
    unsigned exponentMagnitude = 0;
    const auto exponent = std::from_chars(
        scientific.data() + exponentOffset + 2,
        scientific.data() + scientific.size(),
        exponentMagnitude,
        10);
    if (exponent.ec != std::errc{} ||
        exponent.ptr != scientific.data() + scientific.size()) {
        return false;
    }
    const int decimalExponent = exponentSign == '-'
        ? -static_cast<int>(exponentMagnitude)
        : static_cast<int>(exponentMagnitude);
    if (decimalExponent < -4 || decimalExponent > 15)
        return writeRaw(out, scientific);

    const bool negative = scientific.front() == '-';
    std::string digits;
    for (size_t index = negative ? 1 : 0;
         index < exponentOffset; ++index) {
        if (scientific[index] != '.')
            digits.push_back(scientific[index]);
    }
    if (digits.empty())
        return false;

    std::string output;
    if (negative)
        output.push_back('-');
    const int decimalPoint = decimalExponent + 1;
    if (decimalPoint <= 0) {
        output.append("0.");
        output.append(static_cast<size_t>(-decimalPoint), '0');
        output.append(digits);
    } else if (static_cast<size_t>(decimalPoint) >= digits.size()) {
        output.append(digits);
        output.append(
            static_cast<size_t>(decimalPoint) - digits.size(), '0');
        output.append(".0");
    } else {
        output.append(digits, 0, static_cast<size_t>(decimalPoint));
        output.push_back('.');
        output.append(digits, static_cast<size_t>(decimalPoint));
    }
    return writeRaw(out, output);
}

bool
writeBoolean(std::ostream &out, bool value)
{
    return writeRaw(out, value ? "true" : "false");
}

bool
writeBytes(std::ostream &out, const uint8_t *data, size_t size)
{
    if (data == nullptr && size != 0)
        return false;
    if (!out.put('"'))
        return false;
    constexpr char hex[] = "0123456789abcdef";
    for (size_t index = 0; index < size; ++index) {
        if (!out.put(hex[data[index] >> 4]) ||
            !out.put(hex[data[index] & 0xf])) {
            return false;
        }
    }
    return static_cast<bool>(out.put('"'));
}

}
}
}
