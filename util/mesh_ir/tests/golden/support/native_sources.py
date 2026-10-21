import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]


def admission_sources():
    directory = ROOT / "src/dev/ai_mesh"
    tree = ast.parse((directory / "SConscript").read_text())
    groups = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id not in (
                "mesh_binary_sources", "mesh_admission_sources"):
            continue
        values = []
        for element in statement.value.elts:
            if isinstance(element, ast.Starred):
                values.extend(groups[element.value.id])
            else:
                values.append(ast.literal_eval(element))
        groups[target.id] = tuple(values)
    return tuple(directory / name for name in groups["mesh_admission_sources"])
