from dataclasses import replace

from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.scheduled import lower_to_program
from mesh_ir.scheduled.addressing import descriptor_endpoint_span
from tests.unit.test_gate2_scheduled_pipeline import _inputs


class CountedTuple(tuple):
    def __new__(cls, values):
        result = super().__new__(cls, values)
        result.scans = 0
        return result

    def __iter__(self):
        self.scans += 1
        return super().__iter__()


def test_dense_endpoint_references_do_not_scan_complete_record_tables():
    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        program = lower_to_program(lowering, arch, effective, executor).program
    facts = program.semantics
    tables = {name: CountedTuple(getattr(facts, name))
        for name in ('kernel_ops', 'views', 'objects', 'logical_shards', 'kernel_tensors')}
    indexed = replace(facts, **tables)
    for use in facts.endpoint_uses:
        assert descriptor_endpoint_span(indexed, use) == descriptor_endpoint_span(facts, use)
    assert all(table.scans == 0 for table in tables.values())
