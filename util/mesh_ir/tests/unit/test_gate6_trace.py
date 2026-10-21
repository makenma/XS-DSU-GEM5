import json

from mesh_ir.runtime_trace import TraceFilter, write_mesh_trace


def test_trace_filter_preserves_tick_boundaries_and_tensor_membership(tmp_path):
    schedule = {"COMMANDS": [{"command_id": 3, "opcode": 7, "operand_begin": 0, "operand_count": 2}],
                "COMMAND_OPERANDS": [{"tensor_id": 11}, {"tensor_id": 12}], "DMA_DESCRIPTORS": []}
    result = {"instances": [{"instance": 2, "cores": {"7": {"observations": {
        "commands": [{"command_id": 3, "generation": 4, "issued": True,
                      "issue_tick": 10, "terminal": True, "terminal_tick": 20}],
        "descriptor_executions": []}}}}]}
    full, filtered = tmp_path / "full.jsonl", tmp_path / "filtered.jsonl"
    write_mesh_trace(full, schedule, result)
    write_mesh_trace(filtered, schedule, result, TraceFilter(cores=(7,), commands=(3,), tensors=(12,), begin=10, end=10))
    all_rows = [json.loads(line) for line in full.read_text().splitlines()]
    rows = [json.loads(line) for line in filtered.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0] in all_rows
    assert rows[0]["tick"] == 10 and rows[0]["request_id"] == 2
    assert rows[0]["tensor_id"] == 12 and rows[0]["generation"] == 4
    assert all(row["tick"] in (10, 20) for row in all_rows)
