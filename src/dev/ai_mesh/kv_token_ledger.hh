#ifndef DEV_AI_MESH_KV_TOKEN_LEDGER_HH
#define DEV_AI_MESH_KV_TOKEN_LEDGER_HH

#include <cstdint>
#include <map>
#include <optional>
#include <vector>

#include "dev/ai_mesh/mesh_kv_manager.hh"

namespace gem5
{
namespace ai_mesh
{

class KvTokenLedger
{
  public:
    KvTokenLedger(uint64_t request_id, uint64_t session_id,
                  uint64_t kv_handle, uint32_t generation,
                  uint32_t base_tokens, uint32_t append_tokens,
                  uint32_t bytes_per_token);

    bool acceptDescriptor(uint32_t descriptor_id, uint32_t first_token,
                          uint32_t tokens);
    void noteBurst(uint32_t descriptor_id, uint64_t offset_in_append,
                   uint64_t bytes, bool okay,
                   std::optional<KvErrorCandidate> candidate);
    bool descriptorComplete(uint32_t descriptor_id) const;
    uint32_t outstandingDescriptors() const;
    uint32_t acceptDelta() const { return accept_delta; }
    uint32_t terminalDelta() const { return terminal_delta; }
    void noteDescriptorTerminal(uint32_t descriptor_id);
    void clearDeltas();
    std::vector<bool> tokenBitmap() const;
    uint32_t completePrefix() const;
    bool hasFault() const { return !error_candidates.empty(); }
    const std::vector<KvErrorCandidate> &errors() const
    { return error_candidates; }
    KvAppendTerminal appendTerminal() const;

    uint64_t requestId() const { return request_id; }
    uint32_t baseTokenCount() const { return base_tokens; }
    uint32_t appendTokenCount() const { return append_tokens; }
    uint32_t tokenBytes() const { return token_bytes; }

  private:
    struct Entry
    {
        uint32_t first_token = 0;
        uint32_t tokens = 0;
        uint64_t reported_bytes = 0;
        bool terminal = false;
    };

    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    uint32_t base_tokens = 0;
    uint32_t append_tokens = 0;
    uint32_t token_bytes = 0;
    std::map<uint32_t, Entry> descriptors;
    std::vector<uint32_t> committed_bytes;
    std::vector<uint8_t> written_bytes;
    std::vector<KvErrorCandidate> error_candidates;
    uint32_t accept_delta = 0;
    uint32_t terminal_delta = 0;
};

}
}

#endif
