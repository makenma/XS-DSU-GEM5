from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module', params=['identity', 'checkpoint'])
def runtime_probe(tmp_path_factory, request):
    root = Path(__file__).resolve().parents[4]
    work = tmp_path_factory.mktemp('abi2-runtime-checkpoint')
    source = work / 'probe.cc'
    source.write_text(r'''
#include <iostream>
#include <sstream>
#include <boost/archive/binary_iarchive.hpp>
#include <boost/archive/binary_oarchive.hpp>
#include "dev/ai_mesh/dma_command_lifecycle.hh"
#include "dev/ai_mesh/burst_attribution.hh"
#include "dev/ai_mesh/range_image.hh"
#include "dev/ai_mesh/peer_transfer_coverage.hh"
using namespace gem5::ai_mesh;
int main() {
    const uint64_t instance = (uint64_t(1) << 32) + 7;
    const auto descriptor = staticProgramObject(InstanceGeneration(instance), mesh_abi::MeshObjectKind::DESCRIPTOR, 8);
    BurstAttribution burst;
    burst.descriptor = descriptor;
    if (burst.descriptor.instance.value() != instance) { std::cerr << "burst identity was truncated"; return 6; }
    RuntimeObservations observations;
    observations.beginFrame(instance, 3);
    if (observations.instanceId() != instance) { std::cerr << "instance identity was truncated"; return 1; }
    observations.recordCommandAdmission({5, 2}, 10);
    observations.recordEnginePlan({5, 2}, EngineKind::Tensor, 20, 10, 30);
    observations.recordDescriptorSubmission({{5, 2}, descriptor}, 11, 30);
    observations.recordDescriptorIndexRead({{5, 2}, descriptor}, {0, 64, 7, 12, 13, 14});
    observations.recordDescriptorIndexRead({{5, 2}, descriptor}, {1, 68, 2, 15, 16, 17});
    DmaCommandLifecycle lifecycle;
    DmaCommandLifecycle::Admission admitted;
    admitted.command_id = 5; admitted.generation = 2; admitted.descriptors = {8, 9};
    admitted.expected_bytes = 16;
    lifecycle.admit(admitted);
    lifecycle.noteSubmitted(5, 8);
#ifdef CHECKPOINT
    DestinationSegment destination;
    destination.descriptor_id = 8; destination.bytes_hex = "ab12";
    PeerTransferCoverage coverage;
    coverage.transfer_id = 7; coverage.covered_bytes = 2;
    coverage.landing_events.push_back({29, 17, {{100, 102}}});
    std::stringstream bytes;
    { boost::archive::binary_oarchive out(bytes); out << observations << lifecycle << burst << destination << coverage; }
    RuntimeObservations restored;
    DmaCommandLifecycle resumed;
    { boost::archive::binary_iarchive in(bytes); in >> restored >> resumed >> burst >> destination >> coverage; }
    if (burst.descriptor != descriptor || restored.instanceId() != instance || !restored.frameActive() || resumed.nextDescriptor(5) != 9) return 2;
    if (destination.bytes_hex != "ab12" || coverage.landing_events.size() != 1 ||
        coverage.landing_events.front().txn_uid != 17) return 10;
    const auto *gather = restored.findDescriptor({{5, 2}, descriptor});
    if (!gather || gather->index_reads.size() != 2 || gather->index_reads[0].raw_index != 7 ||
        gather->index_reads[1].index_offset != 68 || gather->index_reads[1].read_tick != 17) return 11;
    restored.recordDescriptorIndexRead({{5, 2}, descriptor}, {2, 72, 7, 18, 19, 20});
    if (gather->index_reads[2].occurrence != 2 || gather->index_reads[2].raw_index != 7) return 12;
    restored.recordEngineEnd({5, 2}, 30);
    restored.recordCommandTerminal({5, 2}, 31);
    restored.recordDescriptorCompletion({{5, 2}, descriptor}, 30, true, 29, DmaStatus::OK, {});
    if (restored.view().commands.size() != 1 || restored.view().descriptors.size() != 1 ||
        !restored.findCommand({5, 2})->terminal || !restored.findDescriptor({{5, 2}, descriptor})->completed) return 3;
    const auto first = resumed.retire(5, 8, 8, DmaStatus::OK, 3);
    if (!first.known || first.duplicate || first.drained) return 4;
    resumed.noteSubmitted(5, 9);
    const auto last = resumed.retire(5, 9, 8, DmaStatus::OK, 3);
    if (!last.known || !last.drained || resumed.find(5)->committed_bytes != 16) return 5;
#endif
    DmaCommandLifecycle suppressed;
    DmaCommandLifecycle::Admission publish;
    publish.command_id = 1; publish.descriptors = {2, 3}; publish.transfer_id = 7; publish.expected_bytes = 16;
    suppressed.admit(publish);
    suppressed.noteSubmitted(1, 2);
    const auto quiet = suppressed.retire(1, 2, 8, DmaStatus::SUPPRESSED, 3);
    if (quiet.first_failure || quiet.notify || quiet.drained || !suppressed.find(1)->suppressed ||
        suppressed.find(1)->failed || suppressed.find(1)->committed_bytes) return 7;
    suppressed.noteSubmitted(1, 3);
    const auto tail = suppressed.retire(1, 3, 8, DmaStatus::SUPPRESSED, 3);
    if (!tail.drained || tail.first_failure || tail.notify || suppressed.find(1)->committed_bytes) return 8;
    suppressed.finish(1);
    if (!suppressed.drained()) return 9;
    std::cout << "ACCEPTED";
}
''')
    binary = work / 'probe'
    sources = [root / 'src/dev/ai_mesh' / (name + '.cc') for name in ('mesh_runtime_observations', 'dma_command_lifecycle')]
    sources.extend(root / 'src/base' / (name + '.cc') for name in ('logging', 'cprintf', 'hostinfo', 'str'))
    defines = ['-DCHECKPOINT'] if request.param == 'checkpoint' else []
    result = subprocess.run(['g++', '-std=c++17', *defines, '-I' + str(root / 'src'), '-I' + str(root / 'build/AXI_MESH'), str(source), *map(str, sources), '-lboost_serialization', '-o', str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return binary


def test_runtime_identity_and_checkpoint_preserve_the_same_owners(runtime_probe):
    result = subprocess.run([str(runtime_probe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'
