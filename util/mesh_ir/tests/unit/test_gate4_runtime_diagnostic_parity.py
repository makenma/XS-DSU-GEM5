"""Torch NORM-11: the runtime and the compiler share one diagnostic vocabulary.

The compiler catalog in ``mesh_ir/diagnostics.yaml`` is the single authority.
The C++ runtime consumes a generated enumeration of that catalog, and the
base-Torch runtime sources must not invent codes outside it.
"""

import re
from pathlib import Path

from mesh_ir.diagnostics import ERROR_CODES

REPO = Path(__file__).resolve().parents[4]
RUNTIME = REPO / "src" / "dev" / "ai_mesh"
GENERATED = RUNTIME / "generated" / "mesh_diagnostics.hh"

# The Agent/serving scope owns its own gate system and vocabulary, so it is not
# part of the base Torch runtime parity claim.
_AGENT_SCOPE_PREFIXES = (
    "agent_",
    "gate3_",
    "npu_",
    "msi_",
    "irq_",
    "sq_",
    "host_",
    "moe_",
    "kv_",
)

_ENUM_ENTRY = re.compile(r"^\s*(E_[A-Z0-9_]+)\s*=\s*(\d+)\s*,", re.MULTILINE)
_QUOTED_CODE = re.compile(r'"(E_[A-Z0-9_]+)')


def _generated_catalog():
    text = GENERATED.read_text(encoding="utf-8")
    body = text.split("enum class DiagnosticCode : uint16_t", 1)[1]
    body = body.split("};", 1)[0]
    return [name for name, _ in _ENUM_ENTRY.findall(body)]


def _base_runtime_sources():
    sources = [
        path
        for path in sorted(RUNTIME.glob("*.cc")) + sorted(RUNTIME.glob("*.hh"))
        if not path.name.startswith(_AGENT_SCOPE_PREFIXES)
        and ".test." not in path.name
    ]
    assert sources, "no base-Torch runtime sources found"
    return sources


def test_generated_runtime_catalog_matches_the_compiler_catalog():
    generated = _generated_catalog()
    assert generated, "the generated diagnostic enumeration is empty"
    assert generated == list(ERROR_CODES)


def test_base_runtime_sources_emit_only_catalog_codes():
    catalog = set(ERROR_CODES)
    stray = {}
    for path in _base_runtime_sources():
        for code in _QUOTED_CODE.findall(path.read_text(encoding="utf-8")):
            if code not in catalog:
                stray.setdefault(code, []).append(path.name)
    assert stray == {}


def test_detail_literals_are_scanned_not_only_bare_codes():
    sources = _base_runtime_sources()
    detail = {
        match.group(1)
        for path in sources
        for match in re.finditer(r'"(E_[A-Z0-9_]+):\s', path.read_text(encoding="utf-8"))
    }
    assert detail, "no \"E_CODE: detail\" runtime literal found to scan"
    quoted = {
        code
        for path in sources
        for code in _QUOTED_CODE.findall(path.read_text(encoding="utf-8"))
    }
    assert detail <= quoted
