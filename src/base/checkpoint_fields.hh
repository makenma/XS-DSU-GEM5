#ifndef BASE_CHECKPOINT_FIELDS_HH
#define BASE_CHECKPOINT_FIELDS_HH

#include <optional>
#include <stdexcept>
#include <tuple>
#include <type_traits>

#include <boost/pfr/core.hpp>
#include <boost/serialization/library_version_type.hpp>
#include <boost/serialization/array.hpp>
#include <boost/serialization/deque.hpp>
#include <boost/serialization/map.hpp>
#include <boost/serialization/set.hpp>
#include <boost/serialization/string.hpp>
#include <boost/serialization/unordered_map.hpp>
#include <boost/serialization/utility.hpp>
#include <boost/serialization/vector.hpp>

namespace boost::serialization
{
template<class Archive, class T>
void serialize(Archive &archive, std::optional<T> &value, unsigned)
{
    bool present = value.has_value();
    archive & present;
    if constexpr (Archive::is_loading::value) {
        if (present)
            value.emplace();
        else
            value.reset();
    }
    if (present)
        archive & *value;
}

template<class Archive, class... T>
void serialize(Archive &archive, std::tuple<T...> &value, unsigned)
{
    std::apply([&](auto &...field) { ((archive & field), ...); }, value);
}
}

namespace gem5::checkpoint
{
template<class Archive, class... T>
void fields(Archive &archive, T &...value)
{
    static_assert((!std::is_pointer_v<T> && ...));
    ((archive & value), ...);
}

template<class Archive, class Range>
void existing(Archive &archive, Range &values)
{
    size_t size = values.size();
    archive & size;
    if (size != values.size())
        throw std::runtime_error("E_CHECKPOINT_CONFIG: state population differs");
    for (auto &value : values)
        archive & value;
}

template<class Archive, class T>
void aggregate(Archive &archive, T &value)
{
    boost::pfr::for_each_field(value, [&](auto &field) { fields(archive, field); });
}
}

#define GEM5_CHECKPOINT_FIELDS(...) \
    template<class Archive> void serialize(Archive &archive, unsigned) \
    { ::gem5::checkpoint::fields(archive, __VA_ARGS__); }

#define GEM5_CHECKPOINT_AGGREGATE() \
    template<class Archive> void serialize(Archive &archive, unsigned) \
    { ::gem5::checkpoint::aggregate(archive, *this); }

#endif
