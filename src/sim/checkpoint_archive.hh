#ifndef SIM_CHECKPOINT_ARCHIVE_HH
#define SIM_CHECKPOINT_ARCHIVE_HH

#include <fstream>
#include <iterator>
#include <sstream>
#include <boost/archive/binary_iarchive.hpp>
#include <boost/archive/binary_oarchive.hpp>
#include <boost/crc.hpp>

#include "base/checkpoint_fields.hh"
#include "base/logging.hh"
#include "base/statistics.hh"
#include "sim/eventq.hh"
#include "sim/serialize.hh"

namespace gem5
{
template<class Archive>
void serialize(Archive &archive, Cycles &cycles, unsigned)
{
    uint64_t value = cycles;
    archive & value;
    if constexpr (Archive::is_loading::value)
        cycles = Cycles(value);
}
}

namespace gem5::statistics
{
template<class Archive>
void serialize(Archive &archive, Vector &counter, unsigned)
{
    size_t count = counter.size();
    archive & count;
    fatal_if(count != counter.size(), "E_CHECKPOINT_CONFIG: statistics size differs");
    for (size_t i = 0; i < count; ++i) {
        auto value = counter[i].value();
        archive & value;
        if constexpr (Archive::is_loading::value)
            counter[i] = value;
    }
}

template<class Archive>
void serialize(Archive &archive, Scalar &counter, unsigned)
{
    auto value = counter.value();
    archive & value;
    if constexpr (Archive::is_loading::value)
        counter = value;
}
}

namespace gem5::checkpoint
{
template<class T>
void save(CheckpointOut &cp, const std::string &owner, const T &value)
{
    std::ostringstream stream(std::ios::binary);
    {
        boost::archive::binary_oarchive archive(stream);
        archive << value;
    }
    const auto bytes = stream.str();
    boost::crc_32_type digest;
    digest.process_bytes(bytes.data(), bytes.size());
    paramOut(cp, "state_version", 1);
    paramOut(cp, "state_bytes", bytes.size());
    paramOut(cp, "state_crc", digest.checksum());
    std::ofstream file(CheckpointIn::dir() + owner + ".state", std::ios::binary);
    file.write(bytes.data(), bytes.size());
    file.close();
    fatal_if(!file, "checkpoint state write failed for %s", owner);
}

inline std::string read(CheckpointIn &cp, const std::string &owner)
{
    unsigned version = 0;
    uint64_t size = 0;
    uint32_t crc = 0;
    paramIn(cp, "state_version", version);
    paramIn(cp, "state_bytes", size);
    paramIn(cp, "state_crc", crc);
    fatal_if(version != 1, "checkpoint state version mismatch for %s", owner);
    std::ifstream file(cp.getCptDir() + owner + ".state", std::ios::binary);
    fatal_if(!file, "checkpoint state is missing for %s", owner);
    std::string bytes((std::istreambuf_iterator<char>(file)), {});
    boost::crc_32_type digest;
    digest.process_bytes(bytes.data(), bytes.size());
    fatal_if(size != bytes.size() || crc != digest.checksum(),
             "checkpoint state checksum mismatch for %s", owner);
    return bytes;
}

template<class T>
void restore(std::string &bytes, T &value)
{
    std::istringstream stream(bytes, std::ios::binary);
    try {
        boost::archive::binary_iarchive archive(stream);
        archive >> value;
        fatal_if(stream.peek() != std::char_traits<char>::eof(),
                 "checkpoint state contains trailing data");
    } catch (const std::exception &error) {
        fatal("checkpoint state restore failed: %s", error.what());
    }
    bytes.clear();
}

template<class Archive>
void event(Archive &archive, Event &event, EventManager &owner)
{
    bool scheduled = event.scheduled();
    Tick when = scheduled ? event.when() : 0;
    fields(archive, scheduled, when);
    if constexpr (Archive::is_loading::value) {
        if (event.scheduled())
            owner.deschedule(event);
        if (scheduled) {
            fatal_if(when < curTick(), "checkpoint event is in the past");
            owner.schedule(event, when);
        }
    }
}
}
#endif
