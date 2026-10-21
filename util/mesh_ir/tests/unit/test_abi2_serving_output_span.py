import ast
from dataclasses import replace
from pathlib import Path

from mesh_ir.abi.decoder import decode_program
from mesh_ir.execution_view import build_execution_views
from mesh_ir.generated import abi as mesh_abi


def test_output_span_is_independent_of_transport_row_order():
    path = Path('configs/example/ai_mesh/run_gate6_serving.py')
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'published_output_span')
    scope = {'mesh_abi': mesh_abi, 'build_execution_views': build_execution_views,
             'fatal': lambda text: (_ for _ in ()).throw(AssertionError(text))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
    program = decode_program(Path('tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb').read_bytes())
    reference = scope['published_output_span'](program, 0x100000000)
    assert reference[1] == program.agent_request_profiles[0].host_output_bytes
    reordered = replace(program, commands=tuple(reversed(program.commands)))
    assert scope['published_output_span'](reordered, 0x100000000) == reference
