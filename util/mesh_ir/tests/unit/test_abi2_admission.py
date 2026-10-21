from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from tests.golden.support.native_compile import compile_native_probe
from tests.golden.support.native_sources import admission_sources

from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from tests.unit.test_abi2_execution_views import profiles, profile_program
from tests.unit.test_gate2_authored_facade import _arch


@pytest.fixture(scope='module')
def admission_probe(tmp_path_factory, request):
    root = Path(__file__).resolve().parents[4]
    work = tmp_path_factory.mktemp('abi2-admission')
    mode = getattr(request, 'param', None)
    if mode in ('serving', 'quad', 'serving_static'):
        from mesh_ir.architecture import load_arch
        name = {'quad': 'mesh_4x4_moe.yaml', 'serving': 'mesh_1x2_moe.yaml', 'serving_static': 'mesh_1x2.yaml'}[mode]
        arch = load_arch(root / 'configs/example/ai_mesh/arch' / name)
    else:
        arch = request.getfixturevalue('moe_profiles')[1] if mode == 'moe' else _arch()
    source = work / 'probe.cc'
    setup = ['arch.arch_digest_hex = "' + arch.digest().hex() + '";', 'arch.core_ids = {' + ','.join(map(str, arch.core_ids)) + '};', f'arch.sram_bytes = {arch.sram_bytes}; arch.sram_banks = {arch.sram_banks}; arch.sram_alignment = {arch.sram_base_alignment_bytes}; arch.axi_data_bytes = {arch.axi_data_bytes}; arch.axi_max_burst_beats = {arch.axi_max_burst_beats};']
    setup.append(f'arch.command_rom_entries = {arch.command_rom_entries};')
    setup.append(f'arch.weight_cache_slot_bytes = {arch.sram_weight_cache_slot_bytes};')
    for region_id, region in enumerate(arch.regions):
        kind = {'HBM': 0, 'HOST_SHARED': 1, 'CORE_SRAM_APERTURE': 2}[region.kind]
        setup.append(f'arch.regions.push_back({{{region_id}, {region.base}ull, {region.bytes}ull, {region.tile_stride}ull, {region.tile_bytes}ull, {kind}, {str(kind == 2).lower()}}});')
    for partition in arch.sram_partitions:
        setup.append(f'arch.partitions.push_back({{{partition.kind}, {partition.base}ull, {partition.bytes}ull, {partition.alignment}ull, {partition.metadata_entries}, {partition.max_pinned_entries}}});')
    source.write_text(r'''
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_program_invocation.hh"
#include "dev/ai_mesh/serving_instance_binding.hh"
#include "dev/ai_mesh/serving_fill_inputs.hh"
#include "dev/ai_mesh/mesh_serving_batch.hh"
#include "dev/ai_mesh/peer_transfer_coverage.hh"
#include "dev/ai_mesh/mesh_compute_projection.hh"
using namespace gem5::ai_mesh;
int main(int argc, char **argv) {
    std::ifstream input(argv[1], std::ios::binary);
    MeshBytes bytes((std::istreambuf_iterator<char>(input)), {});
    auto program = std::make_shared<DecodedProgram>();
    MeshLoadError error;
    MeshProgramAdmission admission;
    RuntimeArch arch;
''' + '\n'.join(setup) + r'''
    if (argc > 2 && std::string(argv[2]).find("invoke-") != 0) {
        const std::string mode = argv[2];
        arch.partitions = {{mesh_abi::kSramPartitionKindWEIGHT_CACHE, 0, 8192, 64, 2, 1},
                           {mesh_abi::kSramPartitionKindRUNTIME_SCRATCH, 8192, 4096, 64, 1, 0}};
        arch.weight_cache_slot_bytes = 4096;
        if (mode == "overlap") arch.partitions[1].base = 4096;
        if (mode == "escape") arch.partitions[1].bytes = arch.sram_bytes;
        if (mode == "alignment") arch.partitions[1].alignment = 48;
        if (mode == "duplicate") arch.partitions[1].kind = arch.partitions[0].kind;
        if (mode == "unknown") arch.partitions[1].kind = UINT16_MAX;
        if (mode == "metadata") arch.partitions[0].metadata_entries = 3;
        if (mode == "pinned") arch.partitions[0].max_pinned_entries = 3;
        if (mode == "slot") arch.weight_cache_slot_bytes = 3072;
        if (mode == "orphan_slot") arch.partitions.clear();
        if (mode == "zero_bytes") arch.partitions[0].bytes = 0;
        if (!validateRuntimeArch(arch, error)) {
            std::cout << error.code << ": " << error.message;
            return 1;
        }
        std::cout << "ACCEPTED";
        return 0;
    }
    if (!decodeMeshBinary(bytes, *program, error) ||
        !admitProgram(program, arch, admission, error)) {
        std::cout << error.code << ": " << error.message;
        return 1;
    }
    for (const auto &profile : program->transport.profiles) {
        const auto *view = admission.executionViews().forInstance(profile.entrypoint_id, profile.profile_id);
        if (!view) return 2;
    }
    if (argc > 2) {
        const std::string mode = argv[2];
        auto owned = std::make_shared<MeshProgramAdmission>();
        if (!admitProgram(program, arch, *owned, error)) return 3;
        uint32_t entrypoint = program->transport.profiles.front().entrypoint_id;
        uint32_t profile = mode == "invoke-second" ? program->transport.profiles.back().profile_id : program->transport.profiles.front().profile_id;
        if (mode.find("invoke-fill-") == 0) {
            const auto &instance = *std::find_if(program->transport.agent_instance_profiles.begin(), program->transport.agent_instance_profiles.end(),
                [](const auto &item) { return item.phase == mesh_abi::kPhasePUBLISH && item.member_count == 1; });
            entrypoint = instance.mesh_entrypoint_id;
            profile = instance.mesh_profile_id;
        }
        if (mode == "invoke-ambiguous") { entrypoint = 0; profile = 0; }
        if (mode == "invoke-unknown") profile = UINT32_MAX;
        std::shared_ptr<const MeshProgramInvocation> invocation;
        if (!prepareProgramInvocation(owned, entrypoint, profile, {}, invocation, error)) {
            std::cout << error.code << ": " << error.message;
            return 1;
        }
        if (mode.find("invoke-fill-") == 0) {
            const auto instance_it = std::find_if(program->transport.agent_instance_profiles.begin(), program->transport.agent_instance_profiles.end(),
                [profile](const auto &item) { return item.mesh_profile_id == profile && item.phase == mesh_abi::kPhasePUBLISH; });
            const auto instance = *instance_it;
            const auto &member = program->transport.agent_instance_member_bindings.at(instance.member_binding_first);
            const auto output = owned->context().symbolBinding(entrypoint, profile, member.static_output_symbol_id);
            if (!output) return 4;
            ServingInstanceBinding owner;
            owner.reset(mode == "invoke-fill-single" ? 11 : 0, 2, instance.instance_profile_id);
            if (!owner.installInvocation(invocation) || !owner.bindSlot(output->slot->slot_id, {},
                    {0, output->object->footprint_bytes}, ServingMemberIdentity{11, 2, 0})) return 5;
            const auto &publish = *std::find_if(program->transport.agent_publish_surrogate_bindings.begin(), program->transport.agent_publish_surrogate_bindings.end(),
                [&](const auto &item) { return item.instance_profile_id == instance.instance_profile_id; });
            const auto &descriptor = *std::find_if(program->transport.dma_descriptors.begin(), program->transport.dma_descriptors.end(),
                [&](const auto &item) { return item.command_id == publish.producer_command_id; });
            ServingFillBinding fill;
            fill.request_id = 11; fill.request_generation = 2; fill.instance_profile_id = instance.instance_profile_id;
            fill.allocation_id = publish.allocation_id; fill.producer_command_id = publish.producer_command_id;
            fill.content.assign(descriptor.useful_bytes, 0x5a);
            auto view = invocation->executionView();
            auto inspected = *program;
            if (mode == "invoke-fill-ordered") std::reverse(inspected.transport.commands.begin(), inspected.transport.commands.end());
            auto &altered = inspected.transport.agent_instance_profiles.at(instance_it - program->transport.agent_instance_profiles.begin());
            if (mode == "invoke-fill-wrong_entrypoint") altered.mesh_entrypoint_id = UINT32_MAX;
            if (mode == "invoke-fill-wrong_profile") altered.mesh_profile_id = UINT32_MAX;
            if (mode == "invoke-fill-foreign_command") fill.producer_command_id = UINT32_MAX;
            if (mode == "invoke-fill-wrong_generation") fill.request_generation = 3;
            if (mode == "invoke-fill-short_content") fill.content.pop_back();
            if (mode == "invoke-fill-no_consumer")
                for (auto &range : view.ranges)
                    range.command_ids = {publish.producer_command_id};
            std::string reason;
            auto inputs = ServingFillInputs::freeze(inspected, view, InstanceGeneration(7), owner, {fill}, reason);
            if (!inputs) { std::cout << reason; return 1; }
            fill.content[0] = 0;
            const auto key = staticProgramObject(InstanceGeneration(7), mesh_abi::MeshObjectKind::COMMAND, publish.producer_command_id);
            if (!inputs->contentFor(key) || inputs->contentFor(key)->at(0) != 0x5a) return 6;
            if (inputs->contentFor(staticProgramObject(InstanceGeneration(8), mesh_abi::MeshObjectKind::COMMAND, publish.producer_command_id))) return 7;
            std::cout << "ACCEPTED";
            return 0;
        }
        if (mode.find("invoke-serving-batch") == 0) {
            const auto &profile = program->transport.agent_request_profiles.front();
            const auto phase = mode == "invoke-serving-batch-publish" ? mesh_abi::kPhasePUBLISH :
                mode == "invoke-serving-batch-decode" || mode == "invoke-serving-batch-short" ? mesh_abi::kPhaseDECODE : mesh_abi::kPhasePREFILL;
            const auto &instance = *std::find_if(program->transport.agent_instance_profiles.begin(), program->transport.agent_instance_profiles.end(),
                [phase](const auto &item) { return item.phase == phase && item.member_count == 1; });
            const auto host = std::find_if(arch.regions.begin(), arch.regions.end(), [](const auto &region) { return region.kind == 1; });
            const auto hbm = std::find_if(arch.regions.begin(), arch.regions.end(), [](const auto &region) { return region.kind == 0; });
            if (host == arch.regions.end() || hbm == arch.regions.end()) return 40;
            NpuExecutionRequest request;
            request.requestId = 41; request.sessionId = 42; request.kvHandle = 43; request.generation = 3;
            request.programId = profile.program_id; request.profileId = profile.profile_id;
            request.inputAddress = host->base + 0x200000; request.inputBytes = profile.input_binding_bytes;
            request.outputAddress = host->base + 0x300000; request.outputCapacityBytes = profile.host_output_bytes;
            request.metadataAddress = host->base + 0x400000; request.metadataCapacityBytes = 256;
            KvOwnedSlot slot{41, 42, 43, 3, hbm->base + 0x800000, 4096};
            if (mode == "invoke-serving-batch-short") {
                const auto &member = program->transport.agent_instance_member_bindings[instance.member_binding_first];
                const auto source = owned->context().symbolBinding(instance.mesh_entrypoint_id, instance.mesh_profile_id, member.static_kv_symbol_id);
                slot.bytes = source->object->footprint_bytes;
            }
            KvRuntimeView view;
            view.request_id = 41; view.session_id = 42; view.kv_handle = 43; view.generation = 3;
            view.slot_base = slot.address; view.slot_bytes = slot.bytes;
            view.valid_bytes_at_arm = uint64_t(instance.kv_tokens_before) * profile.kv_bytes_per_token;
            for (const auto &requirement : program->transport.agent_request_binding_requirements) {
                if (requirement.request_program_id != profile.program_id || requirement.request_profile_id != profile.profile_id) continue;
                agent_abi::BindingRecord binding{};
                binding.symbol_id = requirement.symbol_id; binding.kind = requirement.binding_kind; binding.flags = requirement.binding_flags;
                if (binding.kind == agent_abi::kBindingKindHOST_INPUT) { binding.address = request.inputAddress; binding.bytes = request.inputBytes; }
                else if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT) { binding.address = request.outputAddress; binding.bytes = request.outputCapacityBytes; }
                else if (binding.kind == agent_abi::kBindingKindKV_EXTERNAL) { binding.address = 0; binding.bytes = slot.bytes; }
                else {
                    const auto &prefill = program->transport.agent_instance_profiles.front();
                    const auto source = owned->context().symbolBinding(prefill.mesh_entrypoint_id, prefill.mesh_profile_id, binding.symbol_id);
                    if (!source) return 41;
                    const auto *region = arch.region(source->slot->region_id);
                    binding.address = region->base + source->reference->allocation_offset_bytes;
                    binding.bytes = source->reference->allocation_size_bytes;
                }
                request.hostBindings.push_back(binding);
            }
            ServingBatchMember member{&request, 0, slot, instance.primary_kv_symbol_id ? std::optional<KvRuntimeView>(view) : std::nullopt};
            MeshServingBatch batch({1, 2, 5});
            std::string reason;
            const auto frozen = batch.freeze(owned, instance, &member, 1, reason);
            if (mode == "invoke-serving-batch-short") {
                if (frozen != MeshServingBatch::FreezeResult::Invalid || batch.frozen() || batch.memberBindingCount()) return 50;
                if (reason.find("physical AXI") == std::string::npos) { std::cout << reason; return 51; }
                std::cout << "SERVING_SHORT_BACKING_REJECTED";
                return 0;
            }
            if (frozen != MeshServingBatch::FreezeResult::Frozen) {
                std::cout << reason; return 42;
            }
            const auto &bound = batch.executionBinding();
            if (!bound.invocation() || batch.memberBindingCount() != size_t(phase == mesh_abi::kPhasePREFILL ? 2 : 1) || batch.weightBindingCount() != 1) return 43;
            for (size_t index = 0; index < batch.memberBindingCount(); ++index) {
                const auto &row = *batch.memberBinding(index);
                const auto *fact = bound.invocation()->binding().binding(row.slot_id);
                if (!fact || fact->allocation_address != row.address) return 44;
                if (row.kind == agent_abi::kBindingKindKV_EXTERNAL &&
                    (fact->allocation_size_bytes != slot.bytes || row.write.bytes != instance.kv_write_bytes_per_member)) return 45;
            }
            const auto saved = batch.memberBinding(0)->address;
            request.inputAddress += 4096;
            if (batch.memberBinding(0)->address != saved) return 46;
            batch.release();
            if (batch.frozen() || batch.executionBinding().invocation()) return 47;
            std::cout << "SERVING_BATCH_PASS";
            return 0;
        }
        if (mode == "invoke-serving-windows") {
            ServingInstanceBinding serving(16);
            serving.reset(41, 3, 11);
            if (serving.resolveSlot(1, 0, 1, false)) return 20;
            if (!serving.installInvocation(invocation)) return 21;
            for (const auto &[slot, fact] : invocation->binding().bindings()) {
                ServingByteWindow read{0, fact.allocation_size_bytes / 2};
                if (!serving.bindSlot(slot, read, {}, ServingMemberIdentity{41, 3, 0})) return 22;
                if (serving.bindSlot(slot, read, {})) return 23;
                const auto access = serving.resolveSlot(slot, 0, read.bytes, false);
                if (!access || !access->member || access->address != fact.allocation_address ||
                    access->member->request_id != 41 || access->member->generation != 3) return 24;
                if (serving.resolveSlot(slot, read.bytes, 1, false) ||
                    serving.resolveSlot(slot, 0, 1, true)) return 25;
            }
            if (serving.bindSlot(UINT32_MAX, {0, 1}, {}) ||
                serving.resolveSlot(UINT32_MAX, 0, 1, false)) return 26;
            serving.reset(42, 4);
            if (serving.invocation() || !serving.empty()) return 27;
            if (!serving.installInvocation(invocation)) return 28;
            for (const auto &[slot, fact] : invocation->binding().bindings()) {
                const ServingByteWindow write = fact.access == mesh_abi::kAccessKindREAD_WRITE ?
                    ServingByteWindow{0, fact.allocation_size_bytes} : ServingByteWindow{};
                if (!serving.bindSlot(slot, {0, fact.allocation_size_bytes}, write,
                                     ServingMemberIdentity{42, 4, 0})) return 29;
            }
            for (const auto &descriptor : program->transport.dma_descriptors) {
                for (bool source : {true, false}) {
                    const auto access = serving.resolveDescriptorRows(descriptor.descriptor_id, source);
                    if (!invocation->binding().hasEndpoint(descriptor.descriptor_id)) {
                        if (access) return 30;
                        continue;
                    }
                    if (!access || access->address != invocation->binding().endpointAddress(descriptor.descriptor_id, source)) return 31;
                }
            }
            if (serving.resolveDescriptorRows(UINT32_MAX, true)) return 32;
            std::cout << "SERVING_WINDOWS_PASS";
            return 0;
        }
        for (const auto &descriptor : program->transport.dma_descriptors) {
            const auto *owner = owned->context().owner("descriptors", descriptor.descriptor_id);
            const bool selected = owner && owner->variant_id == invocation->variantId();
            if (invocation->binding().hasEndpoint(descriptor.descriptor_id) != selected) return 6;
        }
        if (mode == "invoke-compute-projection") {
            for (const auto &command : program->transport.commands) {
                if (command.opcode != mesh_abi::kOpcodeGEMM) continue;
                ComputeCommitRequest request;
                const auto key = staticProgramObject(InstanceGeneration(1), mesh_abi::MeshObjectKind::COMMAND, command.command_id);
                if (!projectComputeCommit(*owned, command, key, request, error)) { std::cerr << error.message; return 70; }
                auto forged = command;
                ComputeCommitRequest rejected;
                if (projectComputeCommit(*owned, forged, key, rejected, error)) return 71;
                for (const auto &span : request.results)
                    std::cout << request.result_allocation_id << " " << span.offset << " " << span.span << "\n";
            }
            return 0;
        }
        if (mode == "invoke-dma-addresses") {
            for (const auto &descriptor : program->transport.dma_descriptors) {
                if (!invocation->binding().hasEndpoint(descriptor.descriptor_id)) continue;
                auto key = staticProgramObject(InstanceGeneration(7), mesh_abi::MeshObjectKind::DESCRIPTOR, descriptor.descriptor_id);
                std::array<uint64_t, 2> addresses;
                if (!invocation->dmaAddresses(descriptor, key, addresses, error)) return 61;
                for (bool source : {false, true}) {
                    if (source && descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) continue;
                    if (addresses[source ? 0 : 1] != invocation->binding().endpointAddress(descriptor.descriptor_id, source)) return 62;
                }
                auto forged = descriptor;
                forged.dst.offset_bytes += 1;
                if (invocation->dmaAddresses(forged, key, addresses, error)) return 63;
                auto dynamic = descriptor;
                for (bool source : {false, true}) {
                    if (source && descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) continue;
                    auto &endpoint = source ? dynamic.src : dynamic.dst;
                    bool overflow = false;
                    const auto base = arch.region(endpoint.region_id)->tileBase(endpoint.owner_core, overflow);
                    if (overflow) return 64;
                    endpoint.offset_bytes = invocation->binding().endpointAddress(descriptor.descriptor_id, source) - base;
                }
                key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
                if (!invocation->dmaAddresses(dynamic, key, addresses, error)) { std::cout << error.message; return 65; }
                for (bool source : {false, true}) {
                    if (source && descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) continue;
                    if (addresses[source ? 0 : 1] != invocation->binding().endpointAddress(descriptor.descriptor_id, source)) return 66;
                }
                dynamic.dst.offset_bytes = UINT64_MAX;
                if (invocation->dmaAddresses(dynamic, key, addresses, error)) return 67;
                key.domain = static_cast<mesh_abi::MeshObjectDomain>(255);
                if (invocation->dmaAddresses(descriptor, key, addresses, error)) return 68;
            }
            std::cout << "ACCEPTED";
            return 0;
        }
        if (mode == "invoke-peer-coverage") {
            PeerTransferCoverageTable coverage;
            coverage.beginInstance();
            for (const auto &[id, plan] : invocation->transferPlans()) {
                coverage.expect(id, plan.ranges);
                if (coverage.expectedBytes(id) != plan.useful_bytes) return 61;
                PeerTransferCoverageTable::CommitFacts facts;
                facts.tick = 1; facts.has_meta = true; facts.txn_uid = id;
                for (const auto &descriptor : program->transport.dma_descriptors) {
                    if (descriptor.transfer_id != id || !invocation->binding().hasEndpoint(descriptor.descriptor_id)) continue;
                    const auto base = invocation->binding().endpointAddress(descriptor.descriptor_id, false);
                    for (uint32_t row = 0; row < descriptor.rows; ++row)
                        for (uint64_t byte = 0; byte < descriptor.row_bytes; ++byte)
                            facts.lanes.push_back(base + row * descriptor.dst_stride_bytes + byte);
                }
                const auto committed = coverage.commit(facts);
                if (committed.newly_covered != plan.useful_bytes || committed.unaccounted || !coverage.notified(id)) return 62;
            }
            std::cout << "ACCEPTED";
            return 0;
        }
        for (const auto core : arch.core_ids) {
            const auto *rom = invocation->rom(core);
            if (!rom) return 4;
            for (const auto stream : rom->streams())
                for (const auto index : *rom->commandIndices(stream)) {
                    const auto &command = program->transport.commands.at(index);
                    const auto *owner = owned->context().owner("commands", command.command_id);
                    if (!owner || owner->variant_id != invocation->variantId()) return 5;
                    if (mode != "invoke-peer") std::cout << command.command_id << " ";
                }
        }
        if (mode == "invoke-peer") {
            for (const auto &[id, plan] : invocation->transferPlans()) {
                std::cout << id << " " << plan.receiver_core << " " << plan.useful_bytes;
                for (const auto &[begin, end] : plan.ranges)
                    std::cout << " " << begin << " " << end - begin;
                std::cout << "\n";
            }
            if (invocation->transferPlan(UINT32_MAX)) return 7;
        }
        return 0;
    }
    std::cout << "ACCEPTED";
}
''')
    names = ['mesh_program_invocation', 'command_rom', 'mesh_invocation_binding',
             'mesh_serving_batch', 'serving_host_bindings', 'publish_segment_ledger',
             'peer_transfer_coverage', 'mesh_compute_projection', 'serving_fill_inputs']
    isl = root.parents[1] / 'worktrees/torch-frontend/.tmp/torch-isl-dev-package/root/usr'
    binary = work / 'probe'
    sources = (*admission_sources(), *[root / "src/dev/ai_mesh" / (name + ".cc") for name in names],
               *[root / "src/base" / (name + ".cc") for name in ("logging", "cprintf", "hostinfo", "str")])
    compile_native_probe(binary, (source, *sources),
        includes=(root / "src", root / "build/AXI_MESH_INTEGRATION", isl / "include"),
        link_flags=("-L" + str(isl / "lib/x86_64-linux-gnu"), "-lisl"))
    return binary


@pytest.mark.parametrize('mode', ['scheduled', 'profile', 'missing_serving_tables', 'missing_moe_tables', 'shifted_profile'])
def test_unified_admission_uses_semantics_and_domain_features(admission_probe, tmp_path, profiles, mode):
    program = profiles if mode == 'scheduled' else profile_program(profiles)
    if mode == 'missing_serving_tables': program = replace(program, required_features=13)
    elif mode == 'missing_moe_tables': program = replace(program, required_features=11)
    elif mode == 'shifted_profile':
        first, *rest = program.profile_stream_ranges
        program = replace(program, profile_stream_ranges=(replace(first, command_begin=first.command_begin+1), *rest))
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    path = tmp_path / 'program.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path)], capture_output=True, text=True)
    if mode in ('scheduled', 'profile'):
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == 'ACCEPTED'
    else:
        assert result.returncode == 1
        assert 'E_ABI_BOUNDS' in result.stdout
        assert ('profile ranges differ' if mode == 'shifted_profile' else 'requires every conditional section') in result.stdout


@pytest.mark.parametrize('mode', ['valid', 'overlap', 'escape', 'alignment', 'duplicate', 'unknown', 'metadata', 'pinned', 'slot', 'orphan_slot', 'zero_bytes'])
def test_cpp_admission_validates_partition_geometry(admission_probe, mode):
    result = subprocess.run([str(admission_probe), '/dev/null', mode], capture_output=True, text=True)
    assert result.returncode == (0 if mode == 'valid' else 1), result.stdout + result.stderr
    assert ('ACCEPTED' if mode == 'valid' else 'E_ARCH_PARTITION') in result.stdout


@pytest.mark.parametrize('mode', ['first', 'second', 'ambiguous', 'unknown'])
@pytest.mark.parametrize('reverse_transport', [False, True])
def test_prepared_invocation_selects_exactly_one_variant(admission_probe, tmp_path, profiles, mode, reverse_transport):
    if reverse_transport:
        profiles = replace(profiles, commands=tuple(reversed(profiles.commands)))
        profiles = replace(profiles, semantic_sha256=semantic_sha256(profiles.semantic_dict()))
    path = tmp_path / 'variants.mshb'
    path.write_bytes(encode_program(profiles))
    result = subprocess.run([str(admission_probe), str(path), 'invoke-' + mode], capture_output=True, text=True)
    if reverse_transport:
        assert result.returncode == 1
        assert 'E_ABI_ORDER' in result.stdout
    elif mode in ('ambiguous', 'unknown'):
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'E_RELOCATION' in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        variant = profiles.semantics.variants[mode == 'second']
        span = variant.membership.commands
        assert list(map(int, result.stdout.split())) == list(range(span.first_id, span.first_id + span.count))


def test_prepared_invocation_retains_admitted_peer_geometry(admission_probe, tmp_path):
    from mesh_ir.golden_programs import build_dual_core_program
    from mesh_ir.ir.common import DmaKind

    arch = _arch()
    program = build_dual_core_program(arch)
    path = tmp_path / 'peer.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path), 'invoke-peer'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = {int(row.split()[0]): list(map(int, row.split()[1:])) for row in result.stdout.splitlines()}
    executions = program.semantics.intrinsic_traffic.descriptors
    expected = {}
    for descriptor in program.dma_descriptors:
        if descriptor.kind != DmaKind.P2P_PUSH or not descriptor.useful_bytes:
            continue
        facts = next(row for row in executions if row.identity.descriptor_id == descriptor.descriptor_id)
        target = expected.setdefault(descriptor.transfer_id, [descriptor.dst.owner_core, 0])
        target[1] += descriptor.useful_bytes
        for row in range(descriptor.rows):
            target.extend([facts.dst_address + row * descriptor.dst_stride_bytes, descriptor.row_bytes])
    assert actual == expected


def test_prepared_peer_plan_is_consumed_by_the_real_coverage_owner(admission_probe, tmp_path):
    from mesh_ir.golden_programs import build_dual_core_program

    program = build_dual_core_program(_arch())
    path = tmp_path / 'peer-coverage.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path), 'invoke-peer-coverage'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'


@pytest.mark.parametrize('factory_name', ['build_fill_view_offset_out_program', 'build_fill_view_offset_row_gap_pattern_program', 'build_fill_view_offset_split_program'])
def test_compute_projection_covers_admitted_consumer_bytes(admission_probe, tmp_path, factory_name):
    from mesh_ir import golden_programs
    from mesh_ir.generated import abi as A

    program = getattr(golden_programs, factory_name)(_arch())
    path = tmp_path / (factory_name + '.mshb')
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path), 'invoke-compute-projection'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = [tuple(map(int, row.split())) for row in result.stdout.splitlines()]
    expected = []
    for descriptor in program.dma_descriptors:
        if descriptor.kind != A.DMA_KIND.STORE:
            continue
        shard = next(item for item in program.shards if item.shard_id == descriptor.src.shard_id)
        allocation = next(item for item in program.allocations if item.allocation_id == shard.allocation_id)
        expected.extend((allocation.allocation_id, allocation.offset_bytes + descriptor.src.offset_bytes + row * descriptor.src_stride_bytes, descriptor.row_bytes) for row in range(descriptor.rows))
    actual_bytes = [(allocation, address + byte) for allocation, address, size in actual for byte in range(size)]
    expected_bytes = [(allocation, address + byte) for allocation, address, size in expected for byte in range(size)]
    assert len(actual_bytes) == len(set(actual_bytes))
    assert sorted(actual_bytes) == sorted(expected_bytes)
    assert len(actual) == 8
    assert {size for _, _, size in actual} == {16}


def test_runtime_dma_domains_use_exact_static_bindings_or_explicit_dynamic_geometry(admission_probe, tmp_path):
    from mesh_ir.golden_programs import build_dual_core_program

    path = tmp_path / 'runtime-addresses.mshb'
    path.write_bytes(encode_program(build_dual_core_program(_arch())) )
    result = subprocess.run([str(admission_probe), str(path), 'invoke-dma-addresses'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'
