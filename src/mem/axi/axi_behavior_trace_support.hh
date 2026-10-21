#ifndef __MEM_AXI_AXI_BEHAVIOR_TRACE_SUPPORT_HH__
#define __MEM_AXI_AXI_BEHAVIOR_TRACE_SUPPORT_HH__

#include <gtest/gtest.h>

#include <boost/archive/binary_iarchive.hpp>
#include <boost/archive/binary_oarchive.hpp>

#include <algorithm>
#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <type_traits>
#include <vector>

#include "base/gtest/logging.hh"
#include "mem/axi/axi_initiator_adapter.hh"
#include "mem/axi/axi_target_adapter.hh"

namespace gem5::axi::behavior
{

class TraceRow
{
  public:
    template<class T>
    TraceRow &field(const std::string &name, const T &value)
    {
        text << '\t' << name << '=';
        if constexpr (std::is_enum_v<T> || std::is_same_v<T, uint8_t>)
            text << static_cast<uint64_t>(value);
        else
            text << value;
        return *this;
    }

    std::string str() const;

  private:
    std::ostringstream text;
};

std::string bytesHex(const std::vector<uint8_t> &bytes);
void append(TraceRow &row, const AxiCommonMeta &meta);
void append(TraceRow &row, const AxiAddressRequest &request);
void append(TraceRow &row, const AxiAddressPacket &packet);
void append(TraceRow &row, const AxiDataPacket &packet);
void append(TraceRow &row, const AxiTargetOccupancy &occupancy);
void append(TraceRow &row, const AxiTargetProgress &progress);

class BehaviorTrace
{
  public:
    explicit BehaviorTrace(const std::string &name);
    void operation(const std::string &name, uint64_t now);
    void record(const std::string &kind, const TraceRow &row);
    void target(const std::string &name, const AxiTargetState &state);
    void source(const std::string &name, const AxiInitiatorState &state);

  private:
    std::ofstream file;
    uint64_t step = 0;
    uint64_t cycle = 0;
};

class CommitRecorder : public AxiWriteCommitObserver,
                       public AxiWritePreCommitPolicy
{
  public:
    CommitRecorder(BehaviorTrace &trace, AxiTargetState &state, std::string name);
    Decision onWritePreCommit(const AxiAddressRequest &request,
        const std::vector<AxiDataPacket> &beats, AxiResp response) override;
    void onAxiWriteCommitted(const AxiAddressRequest &request,
        const std::vector<AxiDataPacket> &beats, AxiResp response) override;
    void onAxiWriteCommittedWithMeta(const AxiAddressPacket &packet,
        const std::vector<AxiDataPacket> &beats, AxiResp response) override;

    std::map<uint64_t, Decision> policies;
    std::vector<uint64_t> uids;
    std::vector<uint64_t> policyBlocked;
    std::vector<uint64_t> observerBlocked;

  private:
    BehaviorTrace &trace;
    AxiTargetState &state;
    const std::string name;
};

AxiTargetConfig targetConfig(bool synthetic, uint32_t depth, uint32_t node = 1);
AxiAddressPacket address(uint64_t uid, uint32_t id, uint64_t sequence,
    uint64_t offset, uint16_t beats = 1, AxiEndpointKey source = {1, 0},
    uint32_t node = 1);
AxiDataPacket writeBeat(const AxiAddressPacket &packet, uint16_t index,
    uint8_t value);

class TargetProbe
{
  public:
    TargetProbe(BehaviorTrace &trace, std::string name,
                const AxiTargetConfig &config);
    bool aw(const AxiAddressPacket &packet, uint64_t now);
    bool w(const AxiDataPacket &packet, uint64_t now);
    bool ar(const AxiAddressPacket &packet, uint64_t now);
    void advance(uint64_t now);
    std::optional<AxiBPacket> b(uint64_t now);
    std::optional<AxiDataPacket> r(uint64_t now);
    void memory(uint64_t address, uint8_t value, uint64_t now);
    void snapshot();
    void drain(uint64_t now);

    AxiTargetState state;
    CommitRecorder first;
    CommitRecorder second;

  private:
    BehaviorTrace &trace;
    const std::string name;
};

class GateSeedArchive
{
  public:
    GateSeedArchive(boost::archive::binary_iarchive &native,
                    std::array<uint64_t, 2> values);

    template<class T>
    GateSeedArchive &operator&(T &value)
    {
        native & value;
        return *this;
    }

    template<class Key, class Compare, class Alloc>
    GateSeedArchive &operator&(
        std::map<Key, uint64_t, Compare, Alloc> &value)
    {
        native & value;
        if (++matchedMaps != 1 || value.size() != values.size())
            throw std::runtime_error("unexpected AXI gate seed map");
        for (const auto &entry : value) {
            if (entry.second != 1)
                throw std::runtime_error("unexpected AXI gate seed history");
        }
        size_t index = 0;
        for (auto &entry : value)
            entry.second = values[index++];
        return *this;
    }

    size_t matchedMaps = 0;

  private:
    boost::archive::binary_iarchive &native;
    const std::array<uint64_t, 2> values;
};

AxiInitiatorConfig sourceConfig(AxiEndpointKey source);

class SourceProbe
{
  public:
    SourceProbe(BehaviorTrace &trace, std::string name,
                const AxiInitiatorConfig &config);
    SourceProbe(BehaviorTrace &trace, std::string name,
                AxiInitiatorState source, uint64_t now);
    AxiAddressPacket issue(bool write, uint32_t id, uint16_t beats,
                           uint64_t now, uint32_t destination = 1);
    void receiveB(const AxiAddressPacket &address, Tick ready,
                  uint64_t now, AxiResp response = AxiResp::Okay);
    void receiveR(const AxiAddressPacket &address, uint16_t index,
                  uint8_t value, Tick ready, uint64_t now);
    std::optional<AxiBBeat> consumeB(uint64_t now);
    std::optional<AxiRBeat> consumeR(uint64_t now);
    void advance(uint64_t now);

    AxiInitiatorState state;

  private:
    void begin(const std::string &operation, uint64_t now);
    void snapshot();

    BehaviorTrace &trace;
    const std::string name;
};

}

#endif
