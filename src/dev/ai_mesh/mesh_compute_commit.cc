#include "dev/ai_mesh/mesh_compute_commit.hh"

#include <algorithm>

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

void
ComputeCommitter::commit(const ComputeCommitRequest &request) const
{
    fatal_if(sram == nullptr, "compute commit has no SRAM");
    fatal_if(request.attribute_bytes != 0 && request.attributes == nullptr,
             "compute commit attributes are missing");
    uint8_t chunk[512];
    uint64_t mix = 0x9E3779B97F4A7C15ull ^
                   uint64_t(request.opcode) * 0x100000001B3ull;
    for (uint32_t byte = 0; byte < request.attribute_bytes; byte++)
        mix = (mix ^ request.attributes[byte]) * 0x100000001B3ull;
    for (const ComputeSpan &input : request.inputs) {
        for (uint64_t done = 0; done < input.span; done += sizeof(chunk)) {
            const uint64_t take = std::min<uint64_t>(sizeof(chunk),
                                                     input.span - done);
            fatal_if(!sram->read(input.offset + done, take, chunk),
                     "compute digest read escapes the SRAM view");
            for (uint64_t byte = 0; byte < take; byte++)
                mix = (mix ^ chunk[byte]) * 0x100000001B3ull;
        }
    }
    ComputeDigest digest;
    digest.command = request.command;
    digest.allocation_id = request.result_allocation_id;
    if (!request.results.empty())
        digest.offset = request.results.front().offset;
    for (int word = 0; word < 4; word++) {
        mix ^= mix >> 30;
        mix *= 0xBF58476D1CE4E5B9ull;
        mix ^= mix >> 27;
        mix *= 0x94D049BB133111EBull;
        mix ^= mix >> 31;
        digest.digest_words[word] =
            uint32_t(mix >> 32) | uint32_t(mix & 0xFFFFFFFF);
    }
    if (request.identity_copy) {
        fatal_if(request.inputs.size() != 1,
                 "an identity copy needs exactly one contributor row");
        const ComputeSpan &source = request.inputs.front();
        for (const ComputeSpan &result : request.results) {
            fatal_if(result.span == 0 || source.span == 0 ||
                         result.span % source.span != 0,
                     "an identity copy result is not a whole number of "
                     "contributor rows");
            for (uint64_t done = 0; done < result.span;
                 done += source.span) {
                for (uint64_t offset = 0; offset < source.span;
                     offset += sizeof(chunk)) {
                    const uint64_t take = std::min<uint64_t>(
                        sizeof(chunk), source.span - offset);
                    fatal_if(!sram->read(source.offset + offset, take, chunk),
                             "identity copy read escapes the SRAM view");
                    fatal_if(!sram->write(result.offset + done + offset,
                                          take, chunk),
                             "identity copy write escapes the SRAM view");
                }
            }
        }
    } else {
        uint64_t stream = mix;
        for (const ComputeSpan &result : request.results) {
            for (uint64_t done = 0; done < result.span;
                 done += sizeof(chunk)) {
                const uint64_t take = std::min<uint64_t>(sizeof(chunk),
                                                         result.span - done);
                for (uint64_t byte = 0; byte < take; byte++) {
                    stream = (stream ^ uint64_t(done + byte)) *
                             0x100000001B3ull;
                    chunk[byte] = uint8_t(stream >> 56);
                }
                fatal_if(!sram->write(result.offset + done, take, chunk),
                         "compute result write escapes the SRAM view");
            }
        }
    }
    for (uint32_t allocation_id : request.valid_allocations) {
        const auto allocation = sram->allocations.find(allocation_id);
        if (allocation != sram->allocations.end())
            allocation->second.valid = true;
    }
    if (digests != nullptr)
        digests->push_back(digest);
}

} // namespace ai_mesh
} // namespace gem5
