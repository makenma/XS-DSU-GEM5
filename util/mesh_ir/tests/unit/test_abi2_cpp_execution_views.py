from pathlib import Path
import subprocess
from dataclasses import replace

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.unit.test_abi2_execution_views import profiles, profile_program


@pytest.fixture(scope='module')
def probe(tmp_path_factory):
    root = Path(__file__).resolve().parents[4]
    work = tmp_path_factory.mktemp('abi2-cpp-views')
    source = work / 'probe.cc'
    source.write_text(r'''
#include <fstream>
#include <algorithm>
#include <iostream>
#include <iterator>
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_execution_view.hh"
#include "dev/ai_mesh/mesh_serving_capacity.hh"
#include "dev/ai_mesh/mesh_ir_semantic_context.hh"
using namespace gem5::ai_mesh;
int main(int argc, char **argv) {
    std::ifstream input(argv[1], std::ios::binary);
    MeshBytes image((std::istreambuf_iterator<char>(input)), {});
    DecodedProgram program;
    ProgramSemanticContext context;
    ExecutionViewSet views;
    MeshLoadError error;
    if (!decodeMeshBinary(image, program, error) ||
        !buildProgramSemanticContext(program, context, error) ||
        !buildExecutionViews(program, context, views, error)) {
        std::cout << error.code << ": " << error.message;
        return 1;
    }
    if (argc > 2) {
        const auto &first = program.transport.profiles.at(0);
        const auto &second = program.transport.profiles.at(1);
        const auto *one = views.forInstance(first.entrypoint_id, first.profile_id);
        const auto *two = views.forInstance(second.entrypoint_id, second.profile_id);
        program.transport.moe_dynamic_regions.resize(3);
        const uint32_t ids[] = {one->ranges.at(0).command_ids.at(0),
            one->ranges.at(0).command_ids.at(1), two->ranges.at(0).command_ids.at(0)};
        for (size_t i = 0; i < 3; ++i) {
            program.transport.moe_dynamic_regions[i].layer_id = i + 1;
            program.transport.moe_dynamic_regions[i].insert_after_command_id = ids[i];
            mesh_abi::MoeExpertSpec expert{};
            expert.layer_id = i + 1;
            expert.core_id = 0;
            expert.weight_symbol_id = 1;
            expert.weight_bytes = 64;
            expert.weight_region_offset = i == 2 ? 128 : 0;
            program.transport.moe_expert_specs.push_back(expert);
        }
        std::reverse(program.transport.commands.begin(), program.transport.commands.end());
        const auto demand = servingStrictCacheCapacity(program, *one, 0);
        const auto other = servingStrictCacheCapacity(program, *two, 0);
        std::cout << demand.lines << " " << demand.occurrences << " " << other.lines << " " << other.occurrences;
        return 0;
    }
    for (const auto &profile : program.transport.profiles) {
        const auto *view = views.forInstance(profile.entrypoint_id, profile.profile_id);
        if (!view) return 2;
        std::cout << profile.profile_id << ":";
        for (const auto &range : view->ranges)
            for (const auto command : range.command_ids)
                std::cout << " " << command;
        std::cout << "\n";
    }
}
''')
    names = ['mesh_binary', 'mesh_binary_envelope', 'mesh_binary_storage', 'mesh_binary_validation', 'mesh_binary_canonical', 'mesh_canonical', 'mesh_hash', 'mesh_execution_view', 'mesh_ir_semantic_context', 'mesh_serving_capacity']
    binary = work / 'probe'
    result = subprocess.run(['g++', '-std=c++17', '-I' + str(root / 'src'), str(source), *[str(root / 'src/dev/ai_mesh' / (name + '.cc')) for name in names], '-o', str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return binary


@pytest.mark.parametrize('profile_feature', [False, True])
def test_cpp_views_keep_admitted_variants_separate(probe, tmp_path, profiles, profile_feature):
    program = profile_program(profiles) if profile_feature else profiles
    source = tmp_path / 'input.mshb'
    source.write_bytes(encode_program(program))
    result = subprocess.run([str(probe), str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = []
    for variant in program.semantics.variants:
        span = variant.membership.commands
        expected.append(str(variant.profile_id) + ':' + ''.join(' ' + str(i) for i in range(span.first_id, span.first_id + span.count)))
    assert result.stdout.splitlines() == expected


def test_cpp_rejects_profile_range_shift_with_fresh_program_checksum(probe, tmp_path, profiles):
    program = profile_program(profiles)
    first, *rest = program.profile_stream_ranges
    program = replace(program, profile_stream_ranges=(replace(first, command_begin=first.command_begin + 1), *rest))
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    source = tmp_path / 'input.mshb'
    source.write_bytes(encode_program(program))
    result = subprocess.run([str(probe), str(source)], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'profile ranges differ from scheduled variant projection' in result.stdout


def test_cache_capacity_uses_selected_command_identities(probe, tmp_path, profiles):
    source = tmp_path / 'capacity.mshb'
    source.write_bytes(encode_program(profiles))
    result = subprocess.run([str(probe), str(source), 'capacity'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == '1 2 1 1'
