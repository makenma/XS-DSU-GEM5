from __future__ import annotations

import os
import sys


def embedded_python_path() -> str:
    paths = [entry for entry in sys.path if entry]
    paths.extend(entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry)
    return os.pathsep.join(dict.fromkeys(paths))


def runtime_environment() -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("AI_MESH_")}
    environment["PYTHONPATH"] = embedded_python_path()
    return environment
