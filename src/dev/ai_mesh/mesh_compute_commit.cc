#include "dev/ai_mesh/mesh_compute_commit.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_hash.hh"

namespace gem5
{
namespace ai_mesh
{

ComputeCommitResult
ComputeCommitter::commit(const ComputeCommitRequest &request) const
{
    fatal_if(sram == nullptr, "compute commit has no SRAM");
    uint8_t chunk[512];
    uint64_t mix = 0x9E3779B97F4A7C15ull ^
                   uint64_t(request.opcode) * 0x100000001B3ull;
    for (uint8_t byte : request.attributes)
        mix = (mix ^ byte) * 0x100000001B3ull;
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
    ComputeCommitResult captured;
    ComputeDigest &digest = captured.digest;
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
    if (request.copy_mode == ComputeCopyMode::RepeatRow)
        fatal_if(request.inputs.size() != 1 || request.inputs.front().span == 0,
                 "an identity copy needs exactly one nonempty contributor row");
    if (request.copy_mode == ComputeCopyMode::Ordered) {
        uint64_t input_bytes = 0;
        uint64_t result_bytes = 0;
        for (const auto &input : request.inputs) {
            fatal_if(!input.span || input.span > UINT64_MAX - input_bytes,
                     "ordered copy input byte count is invalid");
            input_bytes += input.span;
        }
        for (const auto &result : request.results) {
            fatal_if(!result.span || result.span > UINT64_MAX - result_bytes,
                     "ordered copy result byte count is invalid");
            result_bytes += result.span;
        }
        fatal_if(input_bytes != result_bytes,
                 "ordered copy must preserve its complete logical byte stream");
    }
    size_t source_index = 0;
    uint64_t source_offset = 0;
    uint64_t stream = mix;
    uint64_t emitted = 0;
    size_t merged_count = 0;
    ContentRowObservation merged;
    mesh_hash::Sha256 merged_hash;
    std::vector<uint8_t> merged_bytes;
    for (size_t index = 0; index < request.results.size(); ++index) {
        const auto &result = request.results[index];
        fatal_if(result.span == 0 || !sram->fits(result.offset, result.span),
                 "compute result escapes its physical SRAM");
        if (request.copy_mode == ComputeCopyMode::RepeatRow)
            fatal_if(result.span % request.inputs.front().span != 0,
                     "an identity copy result is not a whole number of contributor rows");
        ContentRowObservation row;
        row.address = result.offset;
        row.size = result.span;
        mesh_hash::Sha256 row_hash;
        std::vector<uint8_t> row_bytes;
        if (merged_count == 0)
            merged.address = result.offset;
        for (uint64_t done = 0; done < result.span;) {
            uint64_t take = std::min<uint64_t>(sizeof(chunk), result.span - done);
            if (request.copy_mode == ComputeCopyMode::RepeatRow) {
                const auto &source = request.inputs.front();
                const uint64_t offset = done % source.span;
                take = std::min(take, source.span - offset);
                fatal_if(!sram->read(source.offset + offset, take, chunk),
                         "identity copy read escapes the SRAM view");
            } else if (request.copy_mode == ComputeCopyMode::Ordered) {
                const auto &source = request.inputs.at(source_index);
                take = std::min(take, source.span - source_offset);
                fatal_if(!sram->read(source.offset + source_offset, take, chunk),
                         "ordered copy read escapes the SRAM view");
                source_offset += take;
                if (source_offset == source.span) {
                    ++source_index;
                    source_offset = 0;
                }
            } else {
                for (uint64_t byte = 0; byte < take; ++byte) {
                    stream = (stream ^ (emitted + byte)) * 0x100000001B3ull;
                    chunk[byte] = uint8_t(stream >> 56);
                }
            }
            fatal_if(!sram->write(result.offset + done, take, chunk),
                     "compute result write escapes the SRAM view");
            row_hash.update(chunk, take);
            merged_hash.update(chunk, take);
            if (byte_limit && result.span <= byte_limit)
                row_bytes.insert(row_bytes.end(), chunk, chunk + take);
            if (byte_limit && merged.size + done + take <= byte_limit)
                merged_bytes.insert(merged_bytes.end(), chunk, chunk + take);
            else
                merged_bytes.clear();
            emitted += take;
            done += take;
        }
        row.digest = mesh_hash::digestHex(row_hash.digest());
        if (!row_bytes.empty())
            row.bytes_hex = mesh_hash::bytesHex(row_bytes.data(), row_bytes.size());
        captured.rows.push_back(std::move(row));
        ++merged_count;
        merged.size += result.span;
        const uint64_t end = result.offset + result.span;
        if (index + 1 == request.results.size() || request.results[index + 1].offset != end) {
            if (merged_count > 1) {
                merged.digest = mesh_hash::digestHex(merged_hash.digest());
                if (byte_limit && merged.size <= byte_limit)
                    merged.bytes_hex = mesh_hash::bytesHex(merged_bytes.data(), merged_bytes.size());
                captured.merged_rows.push_back(std::move(merged));
            }
            merged_count = 0;
            merged = {};
            merged_hash = mesh_hash::Sha256();
            merged_bytes.clear();
        }
    }
    for (uint32_t allocation_id : request.valid_allocations) {
        const auto allocation = sram->allocations.find(allocation_id);
        if (allocation != sram->allocations.end())
            allocation->second.valid = true;
    }
    if (digests != nullptr)
        digests->push_back(digest);
    return captured;
}

} // namespace ai_mesh
} // namespace gem5
