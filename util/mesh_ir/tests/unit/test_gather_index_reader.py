from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def reader_probe(tmp_path_factory):
    from tests.golden.support.native_compile import compile_native_probe

    root = Path(__file__).resolve().parents[4]
    work = tmp_path_factory.mktemp("gather-index-reader")
    source = work / "probe.cc"
    source.write_text(r'''
#include <array>
#include <algorithm>
#include <iostream>
#include "dev/ai_mesh/gather_index.hh"
#include "dev/ai_mesh/tensor_sram.hh"
using namespace gem5::ai_mesh;
struct Port : GatherIndexPort {
    TensorSram sram{256, 4, 16, 16, 16, 16, 1, 1, 1};
    unsigned reserves = 0, reads = 0;
    bool blocked = false;
    std::optional<uint64_t> reserveIndexRead(uint64_t offset, uint64_t tick) override {
        if (blocked) return std::nullopt;
        const auto service = sram.tryReserve(tick, offset, 4, false);
        if (!service) return std::nullopt;
        ++reserves;
        return tick + std::max(uint64_t(1), service->stall_ticks);
    }
    bool readIndexWord(uint64_t offset, std::array<uint8_t, 4> &bytes) override {
        ++reads;
        return sram.read(offset, bytes.size(), bytes.data());
    }
};
int main(int argc, char **argv) {
    const std::string mode = argv[1];
    Port port;
    const std::array<uint32_t, 3> values{7, mode == "invalid" ? UINT32_MAX : 2, 7};
    for (size_t index = 0; index < values.size(); ++index) {
        std::array<uint8_t, 4> bytes{};
        for (size_t lane = 0; lane < 4; ++lane) bytes[lane] = values[index] >> (8 * lane);
        if (!port.sram.write(8 + index * 8, 4, bytes.data())) return 1;
    }
    GatherIndexReader reader({8, 8, mode == "empty" ? 0u : 3u, 32768});
    if (mode == "empty") {
        if (!reader.done() || reader.advance(0, port) || port.reserves || port.reads) return 2;
        return 0;
    }
    port.blocked = true;
    if (reader.advance(0, port) || reader.pending() || port.reserves || port.reads) return 3;
    port.blocked = false;
    if (reader.advance(0, port) || !reader.pending() || port.reserves != 1 || port.reads) return 4;
    if (mode == "cancel") reader.cancel();
    unsigned occurrences = 0;
    for (uint64_t tick = 1; tick < 1000 && !reader.done(); ++tick) {
        const auto read = reader.advance(tick, port);
        if (!read) continue;
        if (read->occurrence != occurrences || read->index_offset != 8 + occurrences * 8 ||
            read->raw_index != values[occurrences] || read->ready_tick <= read->issue_tick ||
            read->read_tick < read->ready_tick) return 5;
        ++occurrences;
    }
    if (!reader.done()) return 6;
    const unsigned expected = mode == "cancel" ? 1 : mode == "invalid" ? 2 : 3;
    if (occurrences != expected || port.reads != expected || port.reserves != expected) return 7;
    if (reader.invalidIndex() != (mode == "invalid") || reader.cancelled() != (mode == "cancel")) return 8;
    if (port.sram.trafficBytes(false) != 4 * expected) return 9;
    if (reader.advance(1001, port) || port.reads != expected || port.reserves != expected) return 10;
    std::cout << "ACCEPTED";
}
''')
    sources = [root / "src/dev/ai_mesh/gather_index.cc"]
    sources += [root / "src/base" / (name + ".cc") for name in ("logging", "cprintf", "hostinfo", "str")]
    return compile_native_probe(work / "probe", (source, *sources),
                                includes=(root / "src", root / "build/AXI_MESH"))


@pytest.mark.parametrize("mode", ["duplicate", "invalid", "cancel", "empty"])
def test_reader_uses_timed_sram_and_preserves_occurrence_order(reader_probe, mode):
    result = subprocess.run([str(reader_probe), mode], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
