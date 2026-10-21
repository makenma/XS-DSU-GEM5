from tests.golden.support.native_sources import admission_sources


def test_cross_language_probes_use_the_production_admission_source_set():
    sources = admission_sources()
    assert len(sources) == len(set(sources))
    assert all(path.is_file() for path in sources)
    names = {path.name for path in sources}
    assert {'mesh_moe_verifier.cc', 'mesh_serving_verifier.cc',
            'mesh_kv_layout.cc', 'mesh_ir_verifier.cc',
            'mesh_ir_semantic_context.cc'} <= names
    assert not {'mesh_program_invocation.cc', 'command_rom.cc'} & names
