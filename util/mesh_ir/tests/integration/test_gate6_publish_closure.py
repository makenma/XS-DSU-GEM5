from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.generated import abi as A
from mesh_ir.serving_profiles import apply_request_profile_keys

from .serving_runtime import FIXTURES, PROBE, run_serving_fixture, write_serving_fixture


def test_loader_rejects_data_consumer_of_suppressible_completion(tmp_path, monkeypatch):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    work = write_serving_fixture(tmp_path, program)
    from mesh_ir.architecture import load_arch
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.serving_programs import full_view_serving_program
    from .serving_runtime import ARCH_YAML

    original = AuthoredMemory.fill

    def fill(memory, name, destination, pattern, **kwargs):
        if name == 'publish-fill:1':
            kwargs['after'] = (memory.effects[-1],)
        return original(memory, name, destination, pattern, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(AuthoredMemory, 'fill', fill)
        mutated = full_view_serving_program(load_arch(ARCH_YAML), output_tokens=2)
    bad = work / "invalid.mshb"
    bad.write_bytes(encode_program(apply_request_profile_keys(mutated)))
    entry = work / "loader_probe.py"
    entry.write_text(
        "import runpy\nfrom m5.objects import MeshProgramLoader\n"
        "original = MeshProgramLoader.__init__\n"
        "def override(self, *args, **kwargs):\n"
        f"    kwargs['program_file'] = {str(bad)!r}\n"
        "    original(self, *args, **kwargs)\n"
        "MeshProgramLoader.__init__ = override\n"
        f"runpy.run_path({str(PROBE)!r}, run_name='__m5_main__')\n")
    run_serving_fixture(work, "rejected", entry=entry,
                        rejection_code="suppressed PUBLISH event reaches a data consumer")


def test_loader_rejects_runtime_fill_without_a_member_store(tmp_path, monkeypatch):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    work = write_serving_fixture(tmp_path, program)
    from mesh_ir.architecture import load_arch
    from mesh_ir.authoring import AuthoredMemory
    from mesh_ir.serving_programs import serving_program
    from .serving_runtime import ARCH_YAML

    original = AuthoredMemory.matmul

    def matmul(memory, name, left, right, destination, **kwargs):
        operation = original(memory, name, left, right, destination, **kwargs)
        memory.fill('orphan-runtime-fill', destination,
                    A.DMA_FILL_RUNTIME_BOUND_SENTINEL.to_bytes(8, 'little'), after=(operation,))
        return operation

    with monkeypatch.context() as patch:
        patch.setattr(AuthoredMemory, 'matmul', matmul)
        program = serving_program(load_arch(ARCH_YAML), output_tokens=2)
    bad = work / "orphan_fill.mshb"
    bad.write_bytes(encode_program(apply_request_profile_keys(program)))
    entry = work / "loader_probe.py"
    entry.write_text(
        "import runpy\nfrom m5.objects import MeshProgramLoader\n"
        "original = MeshProgramLoader.__init__\n"
        "def override(self, *args, **kwargs):\n"
        f"    kwargs['program_file'] = {str(bad)!r}\n"
        "    original(self, *args, **kwargs)\n"
        "MeshProgramLoader.__init__ = override\n"
        f"runpy.run_path({str(PROBE)!r}, run_name='__m5_main__')\n")
    run_serving_fixture(work, "rejected", entry=entry,
                        rejection_code="runtime fill has no unique member STORE")
