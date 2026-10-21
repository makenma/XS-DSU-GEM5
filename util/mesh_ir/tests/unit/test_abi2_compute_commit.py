from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module')
def compute_probe(tmp_path_factory):
    root = Path(__file__).resolve().parents[4]
    work = tmp_path_factory.mktemp('abi2-compute-commit')
    source = work / 'probe.cc'
    source.write_text(r'''
#include <array>
#include <iostream>
#include "dev/ai_mesh/mesh_compute_commit.hh"
#include "dev/ai_mesh/mesh_hash.hh"
using namespace gem5::ai_mesh;
int main(int argc, char **argv) {
    TensorSram contiguous(256, 4, 16, 16, 16, 16, 1, 1, 1);
    TensorSram split(256, 4, 16, 16, 16, 16, 1, 1, 1);
    std::array<uint8_t, 16> input{};
    for (size_t i = 0; i < input.size(); ++i) input[i] = i + 1;
    if (!contiguous.write(0, input.size(), input.data()) || !split.write(0, input.size(), input.data())) return 1;
    const std::string mode = argv[1];
    const bool gap = mode == "gap" || mode == "ordered_gap";
    const bool ordered = mode == "ordered" || mode == "ordered_gap";
    std::array<uint8_t, 16> initial{}; initial.fill(0x5a);
    split.write(64, initial.size(), initial.data());
    ComputeCommitRequest request;
    request.opcode = mesh_abi::kOpcodeGEMM;
    request.inputs = {{0, 16}};
    if (mode == "copy") { request.copy_mode = ComputeCopyMode::RepeatRow; request.inputs = {{0, 4}}; }
    if (ordered) { request.copy_mode = ComputeCopyMode::Ordered; request.inputs = {{8, 3}, {0, 5}}; }
    request.results = {{64, 8}};
    ComputeCommitter(&contiguous, nullptr).commit(request);
    request.results = gap ? std::vector<ComputeSpan>{{64, 2}, {68, 2}, {72, 2}, {76, 2}} : std::vector<ComputeSpan>{{64, 4}, {68, 4}};
    const uint64_t byte_limit = mode == "limit" ? 4 : 64;
    const auto captured = ComputeCommitter(&split, nullptr, byte_limit).commit(request);
    std::array<uint8_t, 8> expected{}, actual{};
    contiguous.read(64, expected.size(), expected.data());
    size_t index = 0;
    for (const auto &span : request.results) {
        if (!split.read(span.offset, span.span, actual.data() + index)) return 2;
        index += span.span;
    }
    if (expected != actual) { std::cerr << "logical output changes when the admitted access is split"; return 3; }
    if (mode == "copy")
        for (size_t i = 0; i < actual.size(); ++i)
            if (actual[i] != input[i % 4]) return 5;
    if (ordered)
        for (size_t i = 0; i < actual.size(); ++i)
            if (actual[i] != (i < 3 ? input[i + 8] : input[i - 3])) return 12;
    if (captured.rows.size() != request.results.size() || captured.digest.offset != 64) return 6;
    for (const auto &row : captured.rows) {
        std::vector<uint8_t> bytes(row.size);
        if (!split.read(row.address, bytes.size(), bytes.data())) return 7;
        mesh_hash::Sha256 hash; hash.update(bytes.data(), bytes.size());
        if (row.digest != mesh_hash::digestHex(hash.digest()) || row.bytes_hex != mesh_hash::bytesHex(bytes.data(), bytes.size())) return 8;
    }
    if (gap ? !captured.merged_rows.empty() : captured.merged_rows.size() != 1) return 9;
    if (!gap) {
        const auto &row = captured.merged_rows.front();
        mesh_hash::Sha256 hash; hash.update(actual.data(), actual.size());
        if (row.address != 64 || row.size != 8 || row.digest != mesh_hash::digestHex(hash.digest())) return 10;
        if (mode == "limit" ? !row.bytes_hex.empty() : row.bytes_hex != mesh_hash::bytesHex(actual.data(), actual.size())) return 11;
    }
    if (gap) {
        std::array<uint8_t, 16> after{};
        split.read(64, after.size(), after.data());
        for (size_t i = 0; i < after.size(); ++i)
            if (i % 4 >= 2 && after[i] != 0x5a) return 4;
    }
    std::cout << "ACCEPTED";
}
''')
    binary = work / 'probe'
    sources = [root / 'src/dev/ai_mesh' / (name + '.cc') for name in ('mesh_compute_commit', 'mesh_hash')]
    sources.extend(root / 'src/base' / (name + '.cc') for name in ('logging', 'cprintf', 'hostinfo', 'str'))
    result = subprocess.run(['g++', '-std=c++17', '-I' + str(root / 'src'), '-I' + str(root / 'build/AXI_MESH'), str(source), *map(str, sources), '-o', str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return binary


@pytest.mark.parametrize('mode', ['split', 'gap', 'copy', 'limit', 'ordered', 'ordered_gap'])
def test_compute_logical_content_is_independent_of_physical_run_partition(compute_probe, mode):
    result = subprocess.run([str(compute_probe), mode], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'
