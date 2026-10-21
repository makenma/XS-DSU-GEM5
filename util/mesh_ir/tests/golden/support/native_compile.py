from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


def compile_native_probe(output, sources, *, includes=(), compile_flags=(), link_flags=()):
    output = Path(output)
    environment = dict(os.environ)
    environment.setdefault("CCACHE_DIR", "/tmp/mesh-ir-native-ccache")
    compiler = ([shutil.which("ccache")] if shutil.which("ccache") else []) + ["g++"]
    flags = ["-std=c++17", *(f"-I{path}" for path in includes), *compile_flags]

    def run(command):
        completed = subprocess.run(command, env=environment, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)

    with TemporaryDirectory(prefix="native-objects-", dir=output.parent) as temporary:
        objects = [Path(temporary) / f"{index}.o" for index in range(len(sources))]
        commands = [compiler + flags + ["-c", str(source), "-o", str(obj)]
                    for source, obj in zip(sources, objects, strict=True)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, commands))
        run(["g++", *flags, *map(str, objects), *link_flags, "-o", str(output)])
    return output
