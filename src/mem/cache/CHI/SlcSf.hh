#ifndef __MEM_CACHE_CHI_SLC_SF_HH__
#define __MEM_CACHE_CHI_SLC_SF_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <ostream>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiPipeline.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/SlcSf.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi
{

/**
 * A configurable shell for the combined SLC and snoop-filter block.
 *
 * The pipeline timing is intentionally separate from lookup/replacement
 * policy. H0-H3 access both tag RAMs, H4-H8 access the L3 data RAM, and
 * H9-H10 are reserved completion stages. The RAM entries are public data
 * structures so protocol behavior can be added without changing storage
 * ownership later.
 */
class SlcSf : public ClockedObject,
              public ruby::Consumer,
              private ChiPipeline<SlcSf, 11, 1>
{
  public:
    PARAMS(SlcSf);

    static constexpr std::size_t NumPipelineStages = 11;
    static constexpr std::size_t NumTagStages = 4;
    static constexpr std::size_t FirstDataStage = 4;
    static constexpr std::size_t NumDataStages = 5;
    static constexpr std::size_t FirstFinishStage = 9;

    // The enum has uint8_t storage, while only values 0-3 are legal (2 bits).
    enum class MesiState : uint8_t
    {
        Invalid = 0,
        Shared = 1,
        Exclusive = 2,
        Modified = 3
    };

    struct L3TagEntry
    {
        // Logical field widths: 1 bit, 32 bits, rnf_num bits, and 2 bits.
        bool unique = false;
        uint32_t paTag = 0;
        std::vector<bool> rnfId;
        MesiState mesi = MesiState::Invalid;

        explicit L3TagEntry(std::size_t rnf_num = 1)
            : rnfId(rnf_num, false)
        {}
    };

    struct SfTagEntry
    {
        // Logical field widths: 1 bit, rnf_num bits, 31 bits, 1 bit,
        // ceil(log2(rnf_num)) bits, and 2 bits.
        bool slcIsSource = false;
        std::vector<bool> sfRnfVec;
        uint32_t paTag = 0;
        bool unique = false;
        uint32_t rnfId = 0;
        MesiState mesi = MesiState::Invalid;

        explicit SfTagEntry(std::size_t rnf_num = 1);

        void setPaTag(uint32_t value) { paTag = value & PaTagMask; }
        void setRnfId(uint32_t value);
        unsigned rnfIdWidth() const { return _rnfIdWidth; }

        static constexpr uint32_t PaTagMask = 0x7fffffffU;

      private:
        unsigned _rnfIdWidth = 0;
    };

    struct L3DataEntry
    {
        std::vector<uint8_t> bytes;

        explicit L3DataEntry(std::size_t line_size = 64)
            : bytes(line_size, 0)
        {}
    };

    class L3TagRam
    {
      public:
        using Row = std::vector<L3TagEntry>;

        L3TagRam(std::size_t sets, std::size_t ways,
                 std::size_t rnf_num);

        L3TagEntry &at(std::size_t set, std::size_t way);
        const L3TagEntry &at(std::size_t set, std::size_t way) const;
        Row &row(std::size_t set);
        const Row &row(std::size_t set) const;

        std::size_t sets() const { return _sets; }
        std::size_t ways() const { return _ways; }
        std::size_t rnfNum() const { return _rnfNum; }

      private:
        std::size_t _sets;
        std::size_t _ways;
        std::size_t _rnfNum;
        std::vector<Row> entries;
    };

    class SfTagRam
    {
      public:
        using Row = std::vector<SfTagEntry>;

        SfTagRam(std::size_t sets, std::size_t ways,
                 std::size_t rnf_num);

        SfTagEntry &at(std::size_t set, std::size_t way);
        const SfTagEntry &at(std::size_t set, std::size_t way) const;
        Row &row(std::size_t set);
        const Row &row(std::size_t set) const;

        std::size_t sets() const { return _sets; }
        std::size_t ways() const { return _ways; }
        std::size_t rnfNum() const { return _rnfNum; }
        unsigned rnfIdWidth() const { return _rnfIdWidth; }

      private:
        std::size_t _sets;
        std::size_t _ways;
        std::size_t _rnfNum;
        unsigned _rnfIdWidth;
        std::vector<Row> entries;
    };

    class L3DataRam
    {
      public:
        using Row = std::vector<L3DataEntry>;

        L3DataRam(std::size_t sets, std::size_t ways,
                  std::size_t line_size);

        L3DataEntry &at(std::size_t set, std::size_t way);
        const L3DataEntry &at(std::size_t set, std::size_t way) const;
        Row &row(std::size_t set);
        const Row &row(std::size_t set) const;

        std::size_t sets() const { return _sets; }
        std::size_t ways() const { return _ways; }
        std::size_t lineSize() const { return _lineSize; }

      private:
        std::size_t _sets;
        std::size_t _ways;
        std::size_t _lineSize;
        std::vector<Row> entries;
    };

    explicit SlcSf(const Params &p);

    void wakeup() override;
    void print(std::ostream &out) const override;

    // Start a new independent 11-cycle traversal. The incoming stage value
    // belongs to the previous component, so enqueue resets it to H0.
    void enqueue(FlitVariant flit);

    bool hasCompleted() const { return !completedFlits.empty(); }
    std::optional<FlitVariant> popCompleted();

    L3TagRam &l3TagRam() { return m_l3TagRam; }
    const L3TagRam &l3TagRam() const { return m_l3TagRam; }
    SfTagRam &sfTagRam() { return m_sfTagRam; }
    const SfTagRam &sfTagRam() const { return m_sfTagRam; }
    L3DataRam &l3DataRam() { return m_l3DataRam; }
    const L3DataRam &l3DataRam() const { return m_l3DataRam; }

  private:
    friend class ChiPipeline<SlcSf, 11, 1>;

    enum class StageResource
    {
        Tags,
        Data,
        Finish
    };

    void doStageH0(FlitVariant &flit);
    void doStageH1(FlitVariant &flit);
    void doStageH2(FlitVariant &flit);
    void doStageH3(FlitVariant &flit);
    void doStageH4(FlitVariant &flit);
    void doStageH5(FlitVariant &flit);
    void doStageH6(FlitVariant &flit);
    void doStageH7(FlitVariant &flit);
    void doStageH8(FlitVariant &flit);
    void doStageH9(FlitVariant &flit);
    void doStageH10(FlitVariant &flit);

    void advanceStage(FlitVariant &flit, std::size_t stage,
                      StageResource resource);
    void accessTagRams(const FlitVariant &flit, std::size_t stage);
    void accessDataRam(const FlitVariant &flit, std::size_t stage);
    static std::optional<uint64_t> addressOf(const FlitVariant &flit);
    static unsigned ceilLog2(std::size_t value);

    const std::size_t cacheLineSize;
    L3TagRam m_l3TagRam;
    SfTagRam m_sfTagRam;
    L3DataRam m_l3DataRam;
    std::deque<FlitVariant> completedFlits;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_SLC_SF_HH__
