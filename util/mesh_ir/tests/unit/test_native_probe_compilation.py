import subprocess

import pytest

from tests.golden.support.native_compile import compile_native_probe


def test_real_dependency_change_invalidates_compiled_probe(tmp_path):
    header = tmp_path / "value.hh"
    header.write_text("#define VALUE 3\n")
    library = tmp_path / "library.cc"
    library.write_text('#include "value.hh"\nint value() { return VALUE; }\n')
    source = tmp_path / "main.cc"
    source.write_text("int value(); int main() { return value(); }\n")
    output = tmp_path / "probe"
    for value in (3, 7):
        header.write_text(f"#define VALUE {value}\n")
        compile_native_probe(output, (source, library), includes=(tmp_path,))
        assert subprocess.run([output]).returncode == value
    library.write_text("invalid C++\n")
    with pytest.raises(RuntimeError, match="error:"):
        compile_native_probe(output, (source, library), includes=(tmp_path,))
