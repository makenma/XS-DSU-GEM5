from pathlib import Path

import pytest

from tests.golden.support.native_sources import admission_sources
from tests.golden.support.native_compile import compile_native_probe


REPO = Path(__file__).resolve().parents[5]
DRIVER_SRC = Path(__file__).with_name("mutation_driver.cc")
INCLUDES = (REPO / "src", REPO / "src/dev/ai_mesh")



@pytest.fixture(scope="session")
def driver(tmp_path_factory):
    executable = tmp_path_factory.mktemp("mutation-driver") / "mutation_driver"
    compile_native_probe(executable, (DRIVER_SRC, *admission_sources()),
        includes=INCLUDES, compile_flags=("-g", "-O1", "-fsanitize=address,undefined",
                                          "-fno-sanitize-recover=all"), link_flags=("-lisl",))
    return str(executable)
