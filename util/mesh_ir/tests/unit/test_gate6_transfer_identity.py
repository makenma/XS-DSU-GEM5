from pathlib import Path

from mesh_ir.architecture import load_arch
from mesh_ir.golden_programs import build_dual_core_program
from mesh_ir.ir.kernel_ir import DmaAttrs, RecvWaitAttrs, KernelMemoryRecords
from mesh_ir.scheduled.records import MemoryIdBases, globalize_memory_records


def test_variant_globalization_rebases_both_transfer_endpoints():
    root = Path(__file__).resolve().parents[4]
    program = build_dual_core_program(load_arch(root / "configs/example/ai_mesh/arch/mesh_1x2.yaml"))
    source = program.semantics
    records = KernelMemoryRecords(source.kernel_tensors, source.computations,
                                  source.placements, source.logical_shards,
                                  source.partial_sums, source.objects, source.views,
                                  source.states, source.tokens, source.kernel_ops)
    bases = MemoryIdBases(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 19)
    projected = globalize_memory_records(records, bases)
    paired = []
    for original, relocated in zip(records.ops, projected.ops):
        if type(original.attrs) in (DmaAttrs, RecvWaitAttrs):
            before, after = original.attrs.transfer_id, relocated.attrs.transfer_id
            assert after == (before + 19 if before else 0)
            if before:
                paired.append(after)
    assert paired and len(set(paired)) * 2 == len(paired)


def test_two_profiles_reuse_local_transfer_ids_and_pass_cpp_admission(tmp_path, verifier_probe):
    import subprocess
    from mesh_ir.abi.encoder import encode_program
    from mesh_ir.golden_programs import build_p2p_reuse_program
    from mesh_ir.scheduled.records import variant_memory_records
    from mesh_ir.scheduled.model import AuthoredProgramOrigin, ControlCommandSource
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.analysis.sram import plan_memory_sram

    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    seed = build_p2p_reuse_program(arch)
    records = variant_memory_records(seed.semantics, seed.semantics.variants[0])
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "transfer-scope", 1))
    semantics = {item.command_id: item for item in seed.semantics.command_semantics}
    for name in ("small", "large"):
        variant = builder.variant("main", name, name, records=records,
                                  allocations=plan_memory_sram(records, arch).allocations)
        for stream in seed.semantics.streams:
            target = variant.stream(stream.core_id, stream.physical_stream_id, stream.flags)
            for command_id in seed.semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]:
                source = semantics[command_id].source
                if type(source) is ControlCommandSource:
                    target.control_command(source.attrs)
                else:
                    target.kernel_command(source.kernel_op_id)
    program = builder.build()
    identities = [op.attrs.transfer_id for op in program.semantics.kernel_ops
                  if type(op.attrs) is RecvWaitAttrs]
    assert len(identities) == len(set(identities))
    for variant in program.semantics.variants:
        assert variant_memory_records(program.semantics, variant) == records
    path = tmp_path / "two-profiles.mshb"
    path.write_bytes(encode_program(program))
    run = subprocess.run([str(verifier_probe), "file", str(path)], capture_output=True, text=True)
    assert run.returncode == 0 and run.stdout == "ACCEPTED", run.stdout + run.stderr
