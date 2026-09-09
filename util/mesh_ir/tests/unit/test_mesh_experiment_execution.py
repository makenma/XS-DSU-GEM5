import json
import os
import sys

from mesh_ir.experiment.execution import ExecutionIdentity, artifact_hashes, run_process, verify_resume


def test_resume_requires_exact_identity_and_unchanged_artifacts(tmp_path):
    artifact = tmp_path / "result.json"
    artifact.write_text('{"result": 1}')
    identity = ExecutionIdentity(*(["a" * 64] * 7))
    record = {"identity": identity.document(), "artifacts": artifact_hashes(tmp_path, [artifact]),
              "returncode": 0, "timed_out": False, "verification": "pass"}
    assert verify_resume(tmp_path, record, identity)
    changed = ExecutionIdentity("b" * 64, *(["a" * 64] * 6))
    assert not verify_resume(tmp_path, record, changed)
    artifact.write_text('{"result": 2}')
    assert not verify_resume(tmp_path, record, identity)


def test_process_timeout_is_failure_with_preserved_log(tmp_path):
    result = run_process([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(3)"],
                         cwd=tmp_path, log_path=tmp_path / "run.log", timeout=.1)
    assert result["timed_out"]
    assert result["returncode"] != 0
    assert "started" in (tmp_path / "run.log").read_text()


def test_process_preserves_exact_command_and_success(tmp_path):
    argv = [sys.executable, "-c", "print('real subprocess')"]
    result = run_process(argv, cwd=tmp_path, log_path=tmp_path / "run.log", timeout=5)
    assert result["argv"] == argv
    assert result["returncode"] == 0
    assert not result["timed_out"]
    record = json.loads((tmp_path / "run.process.json").read_text())
    assert record["status"] == "exited" and record["returncode"] == 0
    assert record["argv"] == argv
    identity = record["identity"]
    assert identity["pid"] == identity["session_id"] == identity["process_group"]
    assert identity["uid"] == os.getuid()
    assert identity["start_ticks"] > 0
    assert identity["boot_id"] and identity["pid_namespace"]


def test_missing_executable_is_structured_launch_failure(tmp_path):
    result = run_process([str(tmp_path / "missing-gem5")], cwd=tmp_path,
                         log_path=tmp_path / "run.log", timeout=5)
    assert result["returncode"] is None
    assert result["timed_out"] is False
    assert result["launch_error"]
    assert (tmp_path / "run.log").exists()
