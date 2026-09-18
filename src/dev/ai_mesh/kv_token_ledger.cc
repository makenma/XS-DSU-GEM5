#include "dev/ai_mesh/kv_token_ledger.hh"

#include <algorithm>

namespace gem5
{
namespace ai_mesh
{

KvTokenLedger::KvTokenLedger(uint64_t request, uint64_t session,
                             uint64_t handle, uint32_t gen, uint32_t base,
                             uint32_t tokens, uint32_t bytes)
    : request_id(request), session_id(session), kv_handle(handle),
      generation(gen), base_tokens(base), append_tokens(tokens),
      token_bytes(bytes), committed_bytes(tokens, 0)
{}

bool
KvTokenLedger::acceptDescriptor(uint32_t descriptor_id, uint32_t first_token,
                                uint32_t tokens)
{
    if (tokens == 0 || first_token >= append_tokens ||
            tokens > append_tokens - first_token)
        return false;
    if (descriptors.count(descriptor_id) != 0)
        return false;
    for (const auto &item : descriptors) {
        const uint32_t begin = item.second.first_token;
        const uint32_t end = begin + item.second.tokens;
        if (first_token < end && begin < first_token + tokens)
            return false;
    }
    Entry entry;
    entry.first_token = first_token;
    entry.tokens = tokens;
    descriptors.emplace(descriptor_id, entry);
    ++accept_delta;
    return true;
}

void
KvTokenLedger::noteBurst(uint32_t descriptor_id, uint64_t offset_in_append,
                         uint64_t bytes, bool okay,
                         std::optional<KvErrorCandidate> candidate)
{
    auto found = descriptors.find(descriptor_id);
    if (found == descriptors.end())
        return;
    Entry &entry = found->second;
    const uint64_t span = uint64_t(entry.tokens) * token_bytes;
    const uint64_t limit = std::min(offset_in_append + bytes,
                                   uint64_t(append_tokens) * token_bytes);
    const uint64_t total = uint64_t(append_tokens) * token_bytes;
    if (written_bytes.size() != total)
        written_bytes.assign(total, 0);
    const uint64_t entry_begin = uint64_t(entry.first_token) * token_bytes;
    const uint64_t entry_end = entry_begin + span;
    for (uint64_t cursor = offset_in_append; cursor < limit; ++cursor) {
        if (!okay || cursor < entry_begin || cursor >= entry_end)
            continue;
        if (written_bytes[cursor] == 0) {
            written_bytes[cursor] = 1;
            ++committed_bytes[cursor / token_bytes];
        }
    }
    if (offset_in_append + bytes > entry.reported_bytes)
        entry.reported_bytes = offset_in_append + bytes;
    if (!okay && candidate)
        error_candidates.push_back(*candidate);
    (void)span;
}

void
KvTokenLedger::noteDescriptorTerminal(uint32_t descriptor_id)
{
    auto found = descriptors.find(descriptor_id);
    if (found == descriptors.end() || found->second.terminal)
        return;
    found->second.terminal = true;
    ++terminal_delta;
}

bool
KvTokenLedger::descriptorComplete(uint32_t descriptor_id) const
{
    const auto found = descriptors.find(descriptor_id);
    if (found == descriptors.end())
        return false;
    for (uint32_t index = 0; index < found->second.tokens; ++index) {
        const uint32_t token = found->second.first_token + index;
        if (committed_bytes[token] != token_bytes)
            return false;
    }
    return true;
}

uint32_t
KvTokenLedger::outstandingDescriptors() const
{
    uint32_t count = 0;
    for (const auto &item : descriptors)
        if (!item.second.terminal)
            ++count;
    return count;
}

void
KvTokenLedger::clearDeltas()
{
    accept_delta = 0;
    terminal_delta = 0;
}

std::vector<bool>
KvTokenLedger::tokenBitmap() const
{
    std::vector<bool> bits;
    bits.reserve(append_tokens);
    for (uint32_t token = 0; token < append_tokens; ++token)
        bits.push_back(committed_bytes[token] == token_bytes);
    return bits;
}

uint32_t
KvTokenLedger::completePrefix() const
{
    uint32_t prefix = 0;
    while (prefix < append_tokens && committed_bytes[prefix] == token_bytes)
        ++prefix;
    return prefix;
}

KvAppendTerminal
KvTokenLedger::appendTerminal() const
{
    KvAppendTerminal terminal;
    terminal.request_id = request_id;
    terminal.tokens_ok = tokenBitmap();
    return terminal;
}

}
}
