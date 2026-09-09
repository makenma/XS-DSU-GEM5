import hashlib
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from mesh_ir.acceptance import atomic_write_json, canonical_digest


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    uid: int
    parent_pid: int
    process_group: int
    session_id: int
    start_ticks: int
    boot_id: str
    pid_namespace: str
    argv: tuple[str, ...]
    cwd: str | None
    executable: str | None

    @classmethod
    def read(cls, pid: int):
        path = Path("/proc") / str(pid)
        stat = (path / "stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        links = {}
        for name in ("cwd", "exe"):
            try:
                links[name] = os.readlink(path / name)
            except FileNotFoundError:
                links[name] = None
        return cls(pid, path.stat().st_uid, int(fields[1]), int(fields[2]), int(fields[3]),
                   int(fields[19]), Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                   os.readlink(path / "ns/pid"),
                   tuple(part.decode(errors="replace") for part in (path / "cmdline").read_bytes().split(b"\0") if part),
                   links["cwd"], links["exe"])


@dataclass(frozen=True)
class ExecutionIdentity:
    source_digest: str
    build_digest: str
    config_digest: str
    workload_digest: str
    map_digest: str
    oracle_digest: str
    profile_digest: str

    @classmethod
    def from_case(cls, case, plan, oracle, source_digest, build_digest, environment):
        normalized = {key: value for key, value in case.items() if key not in ("output_dir", "program_dir", "arch_path")}
        return cls(source_digest, build_digest,
                   canonical_digest({"case": normalized, "environment": environment}),
                   plan["workload_digest"], canonical_digest(case["router_input_vc_depths"] or case["buffer_depths"]),
                   oracle["oracle_digest"], canonical_digest(case["profile"]))

    def document(self):
        return asdict(self)

    def digest(self):
        return canonical_digest(self.document())


def file_digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_identity(repo):
    repo = Path(repo)
    completed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
                                "src", "configs", "util/mesh_ir", "tests/gem5/ai_mesh", "tests/gem5/axi_garnet",
                                "SConstruct", "SConsopts", "build_opts"], cwd=repo, check=True,
                               stdout=subprocess.PIPE)
    names = sorted(set(name.decode("utf-8") for name in completed.stdout.split(b"\0") if name))
    files = {}
    for name in names:
        path = repo / name
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files[name] = file_digest(path) if path.is_file() else "missing"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()
    return {"head": head, "files": files, "digest": canonical_digest(files)}


def artifact_hashes(root, paths):
    root = Path(root).resolve()
    return {str(Path(path).resolve().relative_to(root)): file_digest(path) for path in paths}


def verify_resume(root, record, identity):
    if record.get("identity") != identity.document() or record.get("returncode") != 0 or \
            record.get("timed_out") is not False or record.get("verification") != "pass":
        return False
    root = Path(root).resolve()
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return False
    for name, digest in artifacts.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_digest(path) != digest:
            return False
    return True


def run_process(argv, *, cwd, log_path, timeout, env=None):
    if timeout <= 0:
        raise ValueError("case timeout must be positive")
    argv = list(map(str, argv))
    started = time.monotonic()
    with Path(log_path).open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       env=env, text=True, start_new_session=True)
        except OSError as error:
            log.write(str(error) + "\n")
            return {"argv": argv, "returncode": None, "timed_out": False,
                    "launch_error": str(error), "host_seconds": time.monotonic() - started}
        process_record = {"identity": asdict(ProcessIdentity.read(process.pid)), "argv": argv,
                          "requested_cwd": str(Path(cwd).resolve()), "status": "running"}
        process_path = Path(log_path).with_suffix(".process.json")
        atomic_write_json(process_path, process_record)
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait()
    process_record.update(status="exited", returncode=returncode, timed_out=timed_out)
    atomic_write_json(process_path, process_record)
    return {"argv": argv, "returncode": returncode, "timed_out": timed_out,
            "process_identity": process_record["identity"],
            "host_seconds": time.monotonic() - started}
