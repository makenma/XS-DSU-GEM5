from dataclasses import replace
from random import Random

import pytest

from mesh_ir.diagnostics import MeshIrError
from tests.unit.test_gate2_scheduled_pipeline import _inputs
from tests.unit.test_gate2_fabric_traffic import arch, load_descriptor


def test_dense_memory_lookup_resolves_views_and_rejects_invalid_links():
    _, lowering, _ = _inputs()
    records = lowering.bundle.modules[0].memory_records()
    view = records.views[0]
    tensor = records.tensors[records.shards[view.shard_id - 1].tensor_id - 1]
    assert records.tensor_for_view(view.view_id) is tensor
    for identity in (0, -1, True, len(records.views) + 1):
        with pytest.raises(MeshIrError, match='E_ABI_BOUNDS'):
            records.tensor_for_view(identity)
    broken = replace(records, views=(replace(view, shard_id=0), *records.views[1:]))
    with pytest.raises(MeshIrError, match='E_ABI_BOUNDS'):
        broken.tensor_for_view(view.view_id)


def test_local_memory_records_are_reused_across_program_wrappers():
    from mesh_ir.scheduled.records import variant_memory_records
    from tests.integration.support.workspace_program import build_workspace_program

    program, _ = build_workspace_program()
    semantics = program.semantics
    variant = semantics.variants[0]
    original = variant_memory_records(semantics, variant)
    assert variant_memory_records(replace(semantics), variant) is original
    tensors = (replace(semantics.kernel_tensors[0], name='another-storage'), *semantics.kernel_tensors[1:])
    changed = variant_memory_records(replace(semantics, kernel_tensors=tensors), variant)
    assert changed is not original
    assert changed.tensors[0].name == 'another-storage'
    assert variant_memory_records(semantics, variant).tensors[0].name != 'another-storage'


def test_descriptor_counts_equal_explicit_bursts_at_all_boundaries():
    from mesh_ir.burst_splitter import count_descriptor, plan_descriptor

    random = Random(317)
    for width in (8, 16, 32, 64):
        for limit in (1, 3, 16, 255, 256):
            for _ in range(30):
                row_bytes = random.choice((0, 1, width - 1, width + 1, 4095, 4096, 8193))
                rows = random.randrange(0, 9)
                base = random.randrange(0, 8192)
                stride = random.choice((0, row_bytes, row_bytes + 1, 4096, 8192))
                explicit = plan_descriptor(row_bytes, rows, base, stride, width, limit)
                counted = count_descriptor(row_bytes, rows, base, stride, width, limit)
                assert counted.segments == explicit.segments
                assert counted.bursts == len(explicit.bursts)
                assert counted.beats * width == explicit.beat_bytes
                assert counted.useful_bytes == explicit.useful_bytes
                assert counted.exact
    explicit = plan_descriptor(65, 5003, 17, 67, 64, 3)
    counted = count_descriptor(65, 5003, 17, 67, 64, 3)
    assert counted.bursts == len(explicit.bursts)
    assert counted.beats * 64 == explicit.beat_bytes


@pytest.mark.parametrize('arguments', [
    (2, 2, (1 << 64) - 2, 2, 64, 16),
    ((1 << 64) - 1, 2, 0, 0, 64, 16),
    (64, True, 0, 64, 64, 16),
    (64, 1, 0, 64, 63, 16),
])
def test_counted_descriptor_preserves_scalar_and_overflow_rejection(arguments):
    from mesh_ir.burst_splitter import count_descriptor

    with pytest.raises(MeshIrError):
        count_descriptor(*arguments)


@pytest.mark.parametrize('workers', [2, 32])
def test_parallel_traffic_preserves_canonical_reports_and_admission(arch, load_descriptor, workers):
    import os
    from mesh_ir.analysis.execution import AnalysisExecution
    from mesh_ir.canonical import canonical_json_bytes
    from mesh_ir.traffic import DescriptorExecution, calculate_traffic, verify_traffic_report

    executions = tuple(DescriptorExecution(replace(load_descriptor,
        identity=replace(load_descriptor.identity, descriptor_id=index + 1, command_id=index + 1)), 1)
        for index in range(2048))
    expected = calculate_traffic(arch, 'a' * 64, executions)
    with AnalysisExecution(workers) as execution:
        actual = calculate_traffic(arch, 'a' * 64, executions)
        verify_traffic_report(arch, 'a' * 64, executions, actual)
        with pytest.raises(MeshIrError, match='E_TRAFFIC_MISMATCH'):
            verify_traffic_report(arch, 'a' * 64, executions, replace(actual, semantic_sha256='0' * 64))
    assert canonical_json_bytes(actual) == canonical_json_bytes(expected)
    assert execution.diagnostics.workers == workers
    assert execution.diagnostics.submitted_tasks == execution.diagnostics.completed_tasks
    assert execution.diagnostics.completed_tasks >= 8
    assert all(row.worker_pid != os.getpid() for row in execution.diagnostics.tasks)


def test_parallel_traffic_preserves_the_first_invalid_descriptor(arch, load_descriptor):
    from mesh_ir.analysis.execution import AnalysisExecution
    from mesh_ir.traffic import DescriptorExecution, calculate_traffic

    invalid = replace(load_descriptor, rows=(1 << 32))
    wrong = replace(load_descriptor, kind='LOAD')
    executions = (DescriptorExecution(load_descriptor, 1), DescriptorExecution(invalid, 1), DescriptorExecution(wrong, 1))
    with pytest.raises(MeshIrError) as serial:
        calculate_traffic(arch, 'a' * 64, executions)
    with AnalysisExecution(2):
        with pytest.raises(MeshIrError) as parallel:
            calculate_traffic(arch, 'a' * 64, executions)
    assert (parallel.value.code, parallel.value.message, parallel.value.context) == (
        serial.value.code, serial.value.message, serial.value.context)


def test_analysis_execution_owns_its_scope_and_records_failures():
    from mesh_ir.analysis.execution import AnalysisExecution, current_execution

    assert current_execution() is None
    execution = AnalysisExecution(1)
    with execution:
        assert current_execution() is execution
        with pytest.raises(MeshIrError, match='already active'):
            with AnalysisExecution(2):
                pass
        with pytest.raises(ValueError):
            with execution.stage('RejectedStage'):
                raise ValueError('invalid input')
    assert current_execution() is None
    assert execution.canonical_dict()['stages'][0]['status'] == 'FAIL'
    for workers in (0, -1, True):
        with pytest.raises(MeshIrError):
            AnalysisExecution(workers)


def test_descriptor_counting_handles_large_row_domains_without_materialization():
    from mesh_ir.burst_splitter import count_descriptor

    rows = (1 << 32) - 1
    counted = count_descriptor(64, rows, 0, 64, 64, 16)
    assert (counted.segments, counted.bursts, counted.beats, counted.useful_bytes) == (rows, rows, rows, rows * 64)


def test_scheduling_reuses_identical_producer_memory_facts():
    from mesh_ir.passes.execution import PassExecutor
    from mesh_ir.passes.scheduled import lower_to_program
    from mesh_ir.scheduled.records import variant_memory_records

    arch, lowering, effective = _inputs()
    records = lowering.bundle.modules[0].memory_records()
    with PassExecutor(1) as executor:
        program = lower_to_program(lowering, arch, effective, executor).program
    variant, = program.semantics.variants
    assert variant_memory_records(program.semantics, variant) is records
    changed = replace(records, tensors=(replace(records.tensors[0], name='different'), *records.tensors[1:]))
    projected = variant_memory_records(program.semantics, variant, source_records=changed)
    assert projected is not changed
    assert projected == records


def test_record_equivalence_preserves_scalar_and_enum_types():
    from mesh_ir.analysis.derivation import same_records
    from mesh_ir.ir.common import DType

    _, lowering, _ = _inputs()
    records = lowering.bundle.modules[0].memory_records()
    assert same_records(records, replace(records))
    assert not same_records((True,), (1,))
    assert not same_records((1.0,), (1,))
    assert not same_records((DType.FP16,), (int(DType.FP16),))
    tensor = records.tensors[0]
    altered = replace(records, tensors=(replace(tensor, tensor_id=True), *records.tensors[1:]))
    assert not same_records(records, altered)


def test_traffic_verifier_derives_one_payload_and_rejects_forgery(arch, load_descriptor, monkeypatch):
    import mesh_ir.traffic as traffic

    executions = (traffic.DescriptorExecution(load_descriptor, 1),)
    report = traffic.calculate_traffic(arch, 'a' * 64, executions)
    original = traffic._traffic_payload
    calls = []

    def observed(*arguments):
        calls.append(arguments)
        return original(*arguments)

    monkeypatch.setattr(traffic, '_traffic_payload', observed)
    traffic.verify_traffic_report(arch, 'a' * 64, executions, report)
    assert len(calls) == 1
    with pytest.raises(MeshIrError, match='E_TRAFFIC_MISMATCH'):
        traffic.verify_traffic_report(arch, 'a' * 64, executions, replace(report, semantic_sha256='0' * 64))
