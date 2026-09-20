from __future__ import annotations

import email
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
LOCK = PACKAGE_ROOT / "requirements-lock.txt"


def test_package_metadata_separates_base_compiler_and_tooling_dependencies() -> None:
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = document["project"]

    assert document["build-system"] == {
        "requires": ["hatchling>=1.27,<2", "hatch-vcs>=0.5,<1"],
        "build-backend": "hatchling.build",
    }
    assert project["dynamic"] == ["urls"]
    assert "scripts" not in project
    assert project["requires-python"] == ">=3.12,<3.13"
    assert set(project["dependencies"]) == {
        "jsonschema>=4.23,<5",
        "PyYAML>=6.0.2,<7",
        "referencing==0.37.0",
        "rustworkx==0.18.1",
    }
    assert set(project["optional-dependencies"]["compiler"]) == {
        "sympy==1.14.0",
        "torch==2.8.0+cpu",
    }
    assert set(project["optional-dependencies"]["dev"]) == {
        "build>=1.2.2,<2",
        "hatch-vcs>=0.5,<1",
        "pytest>=8.3,<10",
        "networkx==3.6.1",
        "hatchling>=1.27,<2",
        "uv>=0.12.11,<0.13",
        "wheel>=0.45,<1",
    }
    assert all("torch" not in dependency.lower() for dependency in project["dependencies"])


def test_hatchling_discovers_packages_and_maps_single_source_lock() -> None:
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    wheel = document["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["packages"] == ["mesh_ir"]
    assert wheel["force-include"] == {
        "requirements-lock.txt": "mesh_ir/requirements-lock.txt"
    }
    assert document["tool"]["hatch"]["metadata"]["hooks"]["vcs"]["urls"] == {
        "Compiler Source": "https://github.com/makenma/XS-DSU-GEM5/tree/{commit_hash}"
    }
    assert document["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/mesh_ir",
        "/requirements-lock.txt",
        "/pyproject.toml",
    ]
    assert document["tool"]["hatch"]["build"]["targets"]["sdist"]["force-include"] == {
        "requirements-lock.txt": "requirements-lock.txt"
    }


def test_lock_targets_pinned_compiler_environment_with_verified_hashes() -> None:
    content = LOCK.read_text(encoding="utf-8")

    assert "--python-platform=x86_64-unknown-linux-gnu" in content
    assert "--python-version=3.12.3" in content
    assert "--index-url https://pypi.org/simple" in content
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in content
    blocks = re.findall(
        r"(?ms)^([a-z0-9-]+)==([^ \\\n]+) \\\n(.*?)(?=^[a-z0-9-]+==|\Z)",
        content,
    )
    pins = {name: version for name, version, _ in blocks}

    assert len(blocks) == 34
    assert pins["torch"] == "2.8.0+cpu"
    assert pins["networkx"] == "3.6.1"
    assert pins["numpy"] == "2.5.3"
    assert pins["referencing"] == "0.37.0"
    assert pins["rustworkx"] == "0.18.1"
    assert pins["sympy"] == "1.14.0"
    assert pins["uv"] == "0.12.11"
    assert all(
        re.search(r"--hash=sha256:[0-9a-f]{64}(?: \\\n|\n)", body)
        for _, _, body in blocks
    )


def test_built_wheel_contains_current_modules_resources_and_no_work_trees(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(PACKAGE_ROOT),
        ],
        check=True,
        cwd=tmp_path,
    )

    wheel = next(wheel_dir.glob("mesh_ir-*.whl"))
    expected_modules = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in (PACKAGE_ROOT / "mesh_ir").rglob("*.py")
        if "__pycache__" not in path.parts
    }
    expected_resources = {
        "mesh_ir/diagnostics.yaml",
        "mesh_ir/abi/agent_protocol_abi.yaml",
        "mesh_ir/abi/mesh_ir_abi.yaml",
        "mesh_ir/schemas/mesh_arch_v1.schema.json",
        "mesh_ir/schemas/mesh_compile_v1.schema.json",
        "mesh_ir/schemas/mesh_graph_v1.schema.json",
        "mesh_ir/schemas/mesh_kernel_v1.schema.json",
        "mesh_ir/requirements-lock.txt",
    }

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        metadata_name = next(name for name in members if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(archive.read(metadata_name))

    assert expected_modules <= members
    assert expected_resources <= members
    assert not any(name.endswith(".dist-info/entry_points.txt") for name in members)
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("mesh_ir/requirements-lock.txt") == LOCK.read_bytes()
    assert not any(
        part in {"tests", ".tmp", "__pycache__"} or name.endswith((".pyc", ".pyo"))
        for name in members
        for part in Path(name).parts
    )
    assert set(metadata["Requires-Python"].split(",")) == {">=3.12", "<3.13"}
    requirements = {
        requirement.replace(" ", "").replace("'", '"')
        for requirement in metadata.get_all("Requires-Dist")
    }
    assert 'torch==2.8.0+cpu;extra=="compiler"' in requirements
    assert 'sympy==1.14.0;extra=="compiler"' in requirements
    assert 'networkx==3.6.1;extra=="dev"' in requirements
    assert "referencing==0.37.0" in requirements
    assert "rustworkx==0.18.1" in requirements
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert metadata.get_all("Project-URL") == [
        f"Compiler Source, https://github.com/makenma/XS-DSU-GEM5/tree/{head}"
    ]


def test_sdist_round_trip_preserves_lock_and_source_identity_without_git(tmp_path: Path) -> None:
    sdist_dir = tmp_path / "sdist"
    wheel_dir = tmp_path / "wheel"
    sdist_dir.mkdir()
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--no-isolation",
            "--outdir",
            str(sdist_dir),
            str(PACKAGE_ROOT),
        ],
        check=True,
        cwd=tmp_path,
    )
    sdist = next(sdist_dir.glob("mesh_ir-*.tar.gz"))
    with tarfile.open(sdist) as archive:
        lock_member = next(name for name in archive.getnames() if name.endswith("/requirements-lock.txt"))
        extracted = archive.extractfile(lock_member)
        assert extracted is not None
        assert extracted.read() == LOCK.read_bytes()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(sdist),
        ],
        check=True,
        cwd=tmp_path,
    )
    wheel = next(wheel_dir.glob("mesh_ir-*.whl"))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        installed_metadata = email.message_from_bytes(archive.read(metadata_name))
        assert archive.read("mesh_ir/requirements-lock.txt") == LOCK.read_bytes()
    assert installed_metadata.get_all("Project-URL") == [
        f"Compiler Source, https://github.com/makenma/XS-DSU-GEM5/tree/{head}"
    ]
