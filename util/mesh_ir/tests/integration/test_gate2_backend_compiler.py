from __future__ import annotations

import hashlib
import json
import os
import pickle
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mesh_ir.compiler import BackendCompilationResult, compile_backend
from mesh_ir.passes.execution import PASS_REGISTRY, verify_pass_chain
from mesh_ir.passes.graph_to_kernel import verify_kernel_bundle_correspondence
from mesh_ir.passes.planning_prefix import graph_set_sha256
from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence
from tests.integration.backend_compiler_driver import backend_golden_identity
from tests.integration.backend_compiler_fixtures import ROOT, RealBackendInputs, effective_config, real_backend_inputs


DRIVER = Path(__file__).with_name("backend_compiler_driver.py")
BACKEND_PYTHON = ROOT / ".tmp/torch-gate-2-base-env/bin/python"
GOLDEN = Path(__file__).with_name("backend_compiler_golden.json")
GOLDEN_CONFIGURATIONS = {"tree-tp1", "ring-tp2", "ring-tp4", "tree-tp2", "tree-tp4"}
BACKEND_TIMEOUT_SECONDS = 1800
ORCHESTRATION_CONCURRENCY = 3
DETERMINISM_CASES = tuple((seed, workers) for seed in ("1", "77", "31337") for workers in (1, 2, 8))


def _assert_golden(actual):
    expected = json.loads(GOLDEN.read_text())
    configuration = actual["configuration"]
    expected_actual = {
        "schema_version": expected["schema_version"],
        "architecture_digest": expected["architecture_digest"],
        "source_graphs": expected["source_graphs"],
        "variants": expected["variants"],
        "configuration": {"key": configuration["key"], **expected["configurations"][configuration["key"]]},
    }
    assert actual == expected_actual


def test_backend_golden_fixture_covers_complete_acceptance_matrix():
    document = json.loads(GOLDEN.read_text())

    assert document["schema_version"] == "mesh-backend-golden-v1"
    assert len(document["source_graphs"]) == 2
    assert len(document["variants"]) == 3
    assert set(document["configurations"]) == GOLDEN_CONFIGURATIONS


def test_real_frontends_match_golden_source_graph_and_variant_identities_without_backend_compile(real_backend_inputs):
    document = json.loads(GOLDEN.read_text())

    assert [
        {"entrypoint": graph.entrypoint, "profile_id": graph.profile_id, "semantic_sha256": graph.semantic_sha256}
        for graph in real_backend_inputs.source_graphs
    ] == document["source_graphs"]
    assert [
        {"entrypoint": graph.entrypoint, "profile_id": graph.profile_id, "semantic_sha256": graph.semantic_sha256}
        for graph in real_backend_inputs.variants
    ] == document["variants"]


def _assert_backend(result: BackendCompilationResult, inputs: RealBackendInputs, effective, parent_pid: int) -> None:
    registered = tuple(item.name for item in PASS_REGISTRY)
    expected = registered[registered.index("FuseVerifiedPatterns"):registered.index("ComputeExpectedTraffic") + 1]
    for frontend in inputs.frontends:
        frontend_names = registered[:5]
        assert tuple(item.name for item in frontend.passes) == frontend_names
        assert verify_pass_chain(frontend.passes, frontend.passes[0].input_hash, graph_set_sha256(frontend.variants), frontend_names) is None
    assert result.source_graphs == inputs.source_graphs
    assert result.variants == inputs.variants
    assert tuple((item.entrypoint, item.profile_id) for item in result.bundle.modules) == tuple((item.entrypoint, item.profile_id) for item in inputs.variants)
    assert tuple(item.source_semantic_hash for item in result.bundle.modules) == tuple(item.semantic_sha256 for item in inputs.variants)
    assert tuple(item.name for item in result.passes) == expected
    assert verify_pass_chain(result.passes, graph_set_sha256(inputs.variants), result.program.semantic_sha256, expected) is None
    assert verify_kernel_bundle_correspondence(inputs.variants, result.bundle) is None
    assert verify_program_kernel_correspondence(result.program, result.bundle) is None
    assert verify_program(result.program, inputs.arch).program is result.program
    assert result.kernel.decisions.candidates
    assert len(result.kernel.decisions.placements) == sum(len(function.ops) for graph in inputs.variants for function in graph.functions)
    assert result.program.semantics.intrinsic_traffic.descriptors
    assert result.program.expected_traffic
    assert result.execution.submitted_tasks == result.execution.completed_tasks == len(result.execution.tasks)
    assert result.execution.completed_tasks > 0
    assert all(item.worker_pid is not None and item.worker_pid != parent_pid for item in result.execution.tasks)
    _assert_golden(backend_golden_identity(result, inputs.variants, inputs.source_graphs, inputs.arch, effective))


def _run_driver(python: Path, payload: Path, workers: int, seed: str, *, torch_free: bool = False, candidate: Path | None = None, artifact_directory: Path | None = None, timeout: float = BACKEND_TIMEOUT_SECONDS):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "util/mesh_ir"), PYTHONHASHSEED=seed)
    command = [str(python), str(DRIVER), str(payload), str(workers)]
    if torch_free:
        command.append("--require-no-torch")
    if candidate is not None:
        command.extend(("--golden-candidate", str(candidate)))
    record = None
    if artifact_directory is not None:
        artifact_directory.mkdir()
        image = artifact_directory / "program.mshb"
        command.extend(("--mshb-output", str(image)))
        record = {
            "command": command,
            "cwd": str(Path.cwd().resolve()),
            "environment": {"PYTHONHASHSEED": seed, "PYTHONPATH": env["PYTHONPATH"]},
            "pid": None,
            "seed": seed,
            "started_unix_seconds": time.time(),
            "status": "started",
            "timed_out": False,
            "workers": workers,
        }
        (artifact_directory / "terminal.json").write_text(json.dumps(record, sort_keys=True) + "\n")
    started = time.monotonic()
    stdout = ""
    stderr = ""
    timed_out = False
    launch_error = None
    execution_error = None
    try:
        process = subprocess.Popen(
            command,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if record is not None:
            record["pid"] = process.pid
            (artifact_directory / "terminal.json").write_text(json.dumps(record, sort_keys=True) + "\n")
    except BaseException as error:
        launch_error = error
        returncode = None
    else:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            detail = f"backend driver timed out after {timeout} seconds"
            stderr = f"{stderr}\n{detail}" if stderr else detail
            returncode = 124
            timed_out = True
        except BaseException as error:
            execution_error = error
            returncode = None
    if record is not None:
        stdout_path = artifact_directory / "stdout.txt"
        stderr_path = artifact_directory / "stderr.txt"
        stdout_path.write_text(stdout)
        stderr_path.write_text(stderr)
        record.update(
            {
                "elapsed_seconds": time.monotonic() - started,
                "ended_unix_seconds": time.time(),
                "returncode": returncode,
                "status": "launch_error" if launch_error is not None else "execution_error" if execution_error is not None else "completed",
                "stderr": {"bytes": stderr_path.stat().st_size, "path": stderr_path.name, "sha256": hashlib.sha256(stderr.encode()).hexdigest()},
                "stdout": {"bytes": stdout_path.stat().st_size, "path": stdout_path.name, "sha256": hashlib.sha256(stdout.encode()).hexdigest()},
                "timed_out": timed_out,
            }
        )
        if launch_error is not None:
            record["launch_error"] = f"{type(launch_error).__name__}: {launch_error}"
        if execution_error is not None:
            record["execution_error"] = f"{type(execution_error).__name__}: {execution_error}"
        try:
            document = json.loads(stdout)
            observed = document.get("observed") if isinstance(document, dict) else None
            if isinstance(observed, dict):
                record["observed"] = observed
        except json.JSONDecodeError:
            pass
        image = artifact_directory / "program.mshb"
        if image.is_file():
            binary = image.read_bytes()
            record["image"] = {"bytes": len(binary), "path": image.name, "sha256": hashlib.sha256(binary).hexdigest()}
        (artifact_directory / "terminal.json").write_text(json.dumps(record, sort_keys=True) + "\n")
    if launch_error is not None:
        raise launch_error
    if execution_error is not None:
        raise execution_error
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _run_determinism_matrix(python: Path, payload: Path, artifact_root: Path | None = None):
    if artifact_root is not None:
        artifact_root.mkdir()
    with ThreadPoolExecutor(max_workers=ORCHESTRATION_CONCURRENCY) as pool:
        futures = [
            pool.submit(
                _run_driver,
                python,
                payload,
                workers,
                seed,
                **({"artifact_directory": artifact_root / f"seed-{seed}-workers-{workers}"} if artifact_root is not None else {}),
            )
            for seed, workers in DETERMINISM_CASES
        ]
        return tuple(future.result() for future in futures)


def test_determinism_matrix_has_fixed_order_and_bounded_concurrency(monkeypatch, tmp_path):
    gate = threading.Barrier(ORCHESTRATION_CONCURRENCY)
    lock = threading.Lock()
    active = 0
    maximum = 0

    artifact_root = tmp_path / "mshb-determinism"
    directories = {}

    def run(python, payload, workers, seed, *, artifact_directory=None):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            directories[(seed, workers)] = artifact_directory
        gate.wait(timeout=5)
        with lock:
            active -= 1
        return subprocess.CompletedProcess([], 0, json.dumps([seed, workers]), "")

    monkeypatch.setattr(sys.modules[__name__], "_run_driver", run)
    results = _run_determinism_matrix(Path(sys.executable), tmp_path / "payload", artifact_root)

    assert tuple(tuple(json.loads(item.stdout)) for item in results) == DETERMINISM_CASES
    assert maximum == ORCHESTRATION_CONCURRENCY
    assert directories == {(seed, workers): artifact_root / f"seed-{seed}-workers-{workers}" for seed, workers in DETERMINISM_CASES}


def test_driver_timeout_kills_the_spawned_process_group(monkeypatch, tmp_path):
    driver = tmp_path / "sleeping_driver.py"
    child_pid = tmp_path / "child.pid"
    driver.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "DRIVER", driver)

    artifact_directory = tmp_path / "timeout-artifact"
    result = _run_driver(Path(sys.executable), child_pid, 1, "1", artifact_directory=artifact_directory, timeout=0.5)

    assert result.returncode == 124
    assert "timed out after 0.5 seconds" in result.stderr
    terminal = json.loads((artifact_directory / "terminal.json").read_text())
    stderr = (artifact_directory / terminal["stderr"]["path"]).read_bytes()
    assert terminal["status"] == "completed"
    assert terminal["returncode"] == 124
    assert terminal["timed_out"] is True
    assert terminal["pid"] > 0
    assert stderr == result.stderr.encode()
    assert terminal["stderr"] == {"bytes": len(stderr), "path": "stderr.txt", "sha256": hashlib.sha256(stderr).hexdigest()}
    pid = int(child_pid.read_text())
    status = Path(f"/proc/{pid}/stat")
    assert not status.exists() or status.read_text().split()[2] == "Z"


def test_real_frontends_compile_to_complete_program_tp1(real_backend_inputs):
    effective = effective_config(real_backend_inputs, 1)
    result = compile_backend(
        real_backend_inputs.variants,
        real_backend_inputs.arch,
        effective,
        source_graphs=real_backend_inputs.source_graphs,
        workers=2,
    )
    _assert_backend(result, real_backend_inputs, effective, os.getpid())


@pytest.mark.parametrize(("algorithm", "tensor_parallel"), (("ring", 2), ("ring", 4), ("tree", 2), ("tree", 4)))
def test_real_frontends_compile_collective_sizes(real_backend_inputs, algorithm, tensor_parallel):
    effective = effective_config(real_backend_inputs, tensor_parallel, algorithm)
    result = compile_backend(
        real_backend_inputs.variants,
        real_backend_inputs.arch,
        effective,
        source_graphs=real_backend_inputs.source_graphs,
        workers=2,
    )
    _assert_backend(result, real_backend_inputs, effective, os.getpid())


def test_backend_compiler_is_deterministic_across_hashseeds_and_workers(real_backend_inputs, tmp_path):
    payload = tmp_path / "backend-inputs.pickle"
    effective = effective_config(real_backend_inputs, 2)
    payload.write_bytes(pickle.dumps((real_backend_inputs.variants, real_backend_inputs.source_graphs, real_backend_inputs.arch, effective)))
    artifact_root = tmp_path / "mshb-determinism"
    runs = _run_determinism_matrix(Path(sys.executable), payload, artifact_root)
    assert all(item.returncode == 0 for item in runs), tuple((item.returncode, item.stderr) for item in runs)
    documents = tuple(json.loads(item.stdout) for item in runs)
    assert len(documents) == 9
    assert len({json.dumps(item["semantic"], sort_keys=True, separators=(",", ":")) for item in documents}) == 1
    assert tuple(item["observed"]["workers"] for item in documents) == (1, 2, 8) * 3
    assert all(item["observed"]["submitted"] == item["observed"]["completed"] == item["observed"]["task_count"] > 0 for item in documents)
    assert all(item["observed"]["pipeline_worker_pids"] and item["observed"]["driver_pid"] not in item["observed"]["pipeline_worker_pids"] for item in documents)
    images = []
    for (seed, workers), run, document in zip(DETERMINISM_CASES, runs, documents):
        directory = artifact_root / f"seed-{seed}-workers-{workers}"
        image = directory / "program.mshb"
        terminal = json.loads((directory / "terminal.json").read_text())
        binary = image.read_bytes()
        stdout = (directory / terminal["stdout"]["path"]).read_bytes()
        stderr = (directory / terminal["stderr"]["path"]).read_bytes()
        images.append(binary)
        assert terminal["status"] == "completed"
        assert terminal["returncode"] == 0
        assert terminal["timed_out"] is False
        assert terminal["seed"] == seed
        assert terminal["workers"] == workers
        assert terminal["command"] == run.args
        assert terminal["command"][-2:] == ["--mshb-output", str(image)]
        assert terminal["pid"] == document["observed"]["driver_pid"]
        assert terminal["observed"] == document["observed"]
        assert terminal["image"] == {"bytes": len(binary), "path": image.name, "sha256": hashlib.sha256(binary).hexdigest()}
        assert stdout == run.stdout.encode()
        assert stderr == run.stderr.encode()
        assert terminal["stdout"] == {"bytes": len(stdout), "path": "stdout.txt", "sha256": hashlib.sha256(stdout).hexdigest()}
        assert terminal["stderr"] == {"bytes": len(stderr), "path": "stderr.txt", "sha256": hashlib.sha256(stderr).hexdigest()}
    assert len(images) == 9
    assert all(images)
    assert len(set(images)) == len({hashlib.sha256(image).hexdigest() for image in images}) == 1
    for document in documents:
        _assert_golden(document["golden"])


def test_backend_compiler_runs_in_actual_torch_free_interpreter(real_backend_inputs, tmp_path):
    payload = tmp_path / "torch-free-backend-inputs.pickle"
    effective = effective_config(real_backend_inputs, 2)
    payload.write_bytes(pickle.dumps((real_backend_inputs.variants, real_backend_inputs.source_graphs, real_backend_inputs.arch, effective)))
    run = _run_driver(BACKEND_PYTHON, payload, 2, "31337", torch_free=True)
    assert run.returncode == 0, run.stderr
    document = json.loads(run.stdout)
    assert document["observed"]["workers"] == 2
    assert document["observed"]["environment_probe_pids"]
    assert document["observed"]["pipeline_worker_pids"]
    assert document["observed"]["driver_pid"] not in document["observed"]["environment_probe_pids"]
    assert document["observed"]["driver_pid"] not in document["observed"]["pipeline_worker_pids"]
    assert document["observed"]["submitted"] == document["observed"]["completed"] == document["observed"]["task_count"]
    _assert_golden(document["golden"])
