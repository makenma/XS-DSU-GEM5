#include "dev/ai_mesh/mesh_json.hh"

#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>
#include <vector>
#include <boost/json/basic_parser_impl.hpp>
#include <boost/json/src.hpp>

namespace gem5::ai_mesh
{
uint64_t
meshJsonCounter(double value)
{
    if (!std::isfinite(value) || value < 0 ||
        value > 9007199254740991.0 || std::floor(value) != value)
        throw std::invalid_argument("runtime counter is not an exact nonnegative integer");
    return static_cast<uint64_t>(value);
}

namespace
{
struct UniqueKeys
{
    static constexpr size_t max_array_size = SIZE_MAX;
    static constexpr size_t max_object_size = SIZE_MAX;
    static constexpr size_t max_string_size = SIZE_MAX;
    static constexpr size_t max_key_size = SIZE_MAX;
    std::vector<std::set<std::string>> objects;
    std::string key;
    bool on_document_begin(boost::json::error_code &) { return true; }
    bool on_document_end(boost::json::error_code &) { return true; }
    bool on_object_begin(boost::json::error_code &) { objects.emplace_back(); return true; }
    bool on_object_end(size_t, boost::json::error_code &) { objects.pop_back(); return true; }
    bool on_array_begin(boost::json::error_code &) { return true; }
    bool on_array_end(size_t, boost::json::error_code &) { return true; }
    bool on_key_part(boost::json::string_view part, size_t, boost::json::error_code &)
    { key.append(part.data(), part.size()); return true; }
    bool on_key(boost::json::string_view part, size_t, boost::json::error_code &error)
    {
        key.append(part.data(), part.size());
        const bool inserted = objects.back().insert(key).second;
        key.clear();
        if (!inserted)
            error = boost::json::error::syntax;
        return inserted;
    }
    bool on_string_part(boost::json::string_view, size_t, boost::json::error_code &) { return true; }
    bool on_string(boost::json::string_view, size_t, boost::json::error_code &) { return true; }
    bool on_number_part(boost::json::string_view, boost::json::error_code &) { return true; }
    bool on_int64(int64_t, boost::json::string_view, boost::json::error_code &) { return true; }
    bool on_uint64(uint64_t, boost::json::string_view, boost::json::error_code &) { return true; }
    bool on_double(double, boost::json::string_view, boost::json::error_code &) { return true; }
    bool on_bool(bool, boost::json::error_code &) { return true; }
    bool on_null(boost::json::error_code &) { return true; }
    bool on_comment_part(boost::json::string_view, boost::json::error_code &) { return true; }
    bool on_comment(boost::json::string_view, boost::json::error_code &) { return true; }
};
}

std::optional<boost::json::value>
parseMeshJson(const std::string &text, std::string &reason)
{
    boost::json::error_code error;
    boost::json::basic_parser<UniqueKeys> parser({});
    const size_t consumed = parser.write_some(false, text.data(), text.size(), error);
    if (error || consumed != text.size()) {
        reason = "invalid JSON or duplicate object key";
        return std::nullopt;
    }
    auto value = boost::json::parse(text, error);
    if (error) {
        reason = error.message();
        return std::nullopt;
    }
    reason.clear();
    return value;
}

std::string canonicalMeshJson(const boost::json::value &value)
{
    if (value.is_object()) {
        std::vector<std::string> keys;
        for (const auto &entry : value.as_object())
            keys.emplace_back(entry.key());
        std::sort(keys.begin(), keys.end());
        std::string result = "{";
        for (const auto &key : keys) {
            if (result.size() > 1)
                result += ',';
            result += boost::json::serialize(boost::json::value(key)) + ':' +
                canonicalMeshJson(value.as_object().at(key));
        }
        return result + '}';
    }
    if (value.is_array()) {
        std::string result = "[";
        for (const auto &entry : value.as_array()) {
            if (result.size() > 1)
                result += ',';
            result += canonicalMeshJson(entry);
        }
        return result + ']';
    }
    return boost::json::serialize(value);
}
}
