from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import jsonschema
import yaml

from mesh_ir.generated import agent_abi
from mesh_ir.generated import abi as mesh_abi
from mesh_ir.model import canonical_json_bytes


class ContractError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ContractError(f"duplicate YAML key {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def read_yaml_document(path: Path):
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except ContractError:
        raise
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ContractError(f"invalid YAML document {path}: {error}") from error


SCHEMA_DEFINITIONS = {
    "mandatory_manifest_v1.schema.json": "mandatoryManifest",
    "child_scenario_report_v1.schema.json": "childReport",
    "run_summary_v1.schema.json": "runSummary",
    "run_manifest_v1.schema.json": "runManifest",
    "invariants_v1.schema.json": "invariants",
    "traffic_v1.schema.json": "traffic",
    "fatal_snapshot_v1.schema.json": "fatalSnapshot",
    "mandatory_results_v1.schema.json": "mandatoryResults",
}

ARTIFACT_KINDS = (
    "SUMMARY_JSON",
    "JUNIT_XML",
    "RUN_MANIFEST",
    "INVARIANTS_JSON",
    "TRAFFIC_JSON",
    "TRACE_MIN",
    "WAIT_FOR_GRAPH",
    "FATAL_SNAPSHOT",
)

ARTIFACT_BASENAMES = {
    "SUMMARY_JSON": "summary.json",
    "JUNIT_XML": "junit.xml",
    "RUN_MANIFEST": "run_manifest.json",
    "INVARIANTS_JSON": "invariants.json",
    "TRAFFIC_JSON": "traffic.json",
    "TRACE_MIN": "trace_min.jsonl",
    "WAIT_FOR_GRAPH": "wait_for_graph.json",
    "FATAL_SNAPSHOT": "fatal_snapshot.json",
}

LEDGER_FIELDS = (
    "live_cq_obligations",
    "fatal_cq_obligations",
    "fatal_sq_intakes",
    "fatal_publications",
    "ambiguous_publications",
    "host_ack_wait_b",
    "host_ack_b_error",
    "msi_rob_entries",
    "fatal_records",
)

ZERO_LEDGERS = {name: 0 for name in LEDGER_FIELDS}

CHILD_ENV_BASE = {
    "LC_ALL": "C",
    "TZ": "UTC",
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
}

CHILD_ENV_KEYS = tuple(
    sorted(
        (
            *CHILD_ENV_BASE,
            "AI_MESH_CASE_ID",
            "AI_MESH_SUBCASE",
            "AI_MESH_ARTIFACT_DIR",
            "AI_MESH_CHILD_REPORT",
        ),
        key=lambda value: value.encode("utf-8"),
    )
)

RUN_EXIT_CODES = {
    name: value
    for name, value in vars(agent_abi.RUN_EXIT_REASON).items()
    if not name.startswith("_") and isinstance(value, int)
}

GATE_EXPRESSIONS = {
    1: ("DC-1..16", "DC-24", "DC-27", "DC-29", "DC-34", "DC-35"),
    2: ("DC-17..23", "DC-25", "DC-26", "DC-28", "DC-30..33", "E2E-A", "E2E-B"),
    3: ("PROTO-1..11", "PROTO-13..20", "PROTO-24", "PROTO-26", "PROTO-28..30"),
    4: ("PROTO-21", "PROTO-22", "PROTO-27", "HOST-1..14", "HOST-16..24", "HOST-26"),
    5: ("MOE-1..23", "MOE-25..30", "E2E-C"),
    6: (
        "PROTO-12",
        "PROTO-23",
        "PROTO-25",
        "HOST-15",
        "HOST-25",
        "MOE-24",
        "SERV-1..21",
        "E2E-D",
        "E2E-E",
        "E2E-H",
    ),
    7: ("E2E-F", "E2E-G"),
}

CUMULATIVE = {1: 21, 2: 37, 3: 61, 4: 88, 5: 118, 6: 148, 7: 150}


def _expand_id(expression: str) -> list[str]:
    if ".." not in expression:
        return [expression]
    prefix, bounds = expression.split("-", 1)
    lower, upper = bounds.split("..", 1)
    return [f"{prefix}-{value}" for value in range(int(lower), int(upper) + 1)]


GATE_OF = {
    case_id: gate
    for gate, expressions in GATE_EXPRESSIONS.items()
    for expression in expressions
    for case_id in _expand_id(expression)
}

CANONICAL_IDS = tuple(
    [f"DC-{value}" for value in range(1, 36)]
    + [f"MOE-{value}" for value in range(1, 31)]
    + [f"PROTO-{value}" for value in range(1, 31)]
    + [f"HOST-{value}" for value in range(1, 27)]
    + [f"SERV-{value}" for value in range(1, 22)]
    + [f"E2E-{chr(ord('A') + value)}" for value in range(8)]
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def schema_root() -> Path:
    return repository_root() / "schemas" / "ai_mesh"


def expanded_schema(filename: str) -> dict:
    try:
        definition = SCHEMA_DEFINITIONS[filename]
    except KeyError as error:
        raise ContractError(f"unknown acceptance schema {filename}") from error
    common = json.loads(
        (schema_root() / "acceptance_contract_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "$schema": common["$schema"],
        "$defs": common["$defs"],
        "$ref": f"#/$defs/{definition}",
    }


def validate_schema(filename: str, document) -> None:
    validator = jsonschema.Draft202012Validator(expanded_schema(filename))
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path)
        prefix = f"{location}: " if location else ""
        raise ContractError(f"{filename}: {prefix}{error.message}")


def canonical_digest(document) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def canonical_u64(value) -> int:
    if isinstance(value, bool):
        raise ContractError("u64-json cannot be boolean")
    if isinstance(value, int):
        if 0 <= value <= (1 << 53) - 1:
            return value
        raise ContractError("u64-json integer exceeds exact JSON range")
    if isinstance(value, str) and len(value) == 18 and value.startswith("0x"):
        digits = value[2:]
        if digits == digits.lower() and all(c in "0123456789abcdef" for c in digits):
            decoded = int(digits, 16)
            if decoded > (1 << 53) - 1:
                return decoded
    raise ContractError("non-canonical u64-json value")


def canonical_u64_text(value) -> str:
    return str(canonical_u64(value))


def validate_detail_code(value: dict | None) -> None:
    if value is None:
        return
    symbol = value["symbol"]
    expected = agent_abi.DETAIL_CODE.get(symbol)
    if expected is None or value["value"] != expected:
        raise ContractError(f"detail code symbol/value mismatch for {symbol}")


def validate_manifest(document: dict) -> str:
    validate_schema("mandatory_manifest_v1.schema.json", document)
    cases = document["cases"]
    ids = tuple(case["id"] for case in cases)
    if ids != CANONICAL_IDS:
        raise ContractError("manifest cases are not in canonical 150-ID order")
    for case in cases:
        case_id = case["id"]
        if case["earliest_gate"] != GATE_OF[case_id]:
            raise ContractError(f"{case_id}: earliest_gate mismatch")
        names = [subcase["name"] for subcase in case["subcases"]]
        if len(names) != len(set(names)):
            raise ContractError(f"{case_id}: duplicate subcase name")
        for subcase in case["subcases"]:
            validate_subcase(case_id, subcase)
    for gate, expected in CUMULATIVE.items():
        actual = sum(case["earliest_gate"] <= gate for case in cases)
        if actual != expected:
            raise ContractError(f"gate {gate}: expected {expected} IDs, got {actual}")
    return canonical_digest(document)


def validate_subcase(case_id: str, subcase: dict) -> None:
    artifacts = subcase["artifacts"]
    positions = [ARTIFACT_KINDS.index(kind) for kind in artifacts]
    if positions != sorted(positions):
        raise ContractError(f"{case_id}/{subcase['name']}: artifact order mismatch")
    required = list(ARTIFACT_KINDS[:4])
    if artifacts[:4] != required:
        raise ContractError(f"{case_id}/{subcase['name']}: mandatory artifacts missing")
    runner = subcase["execution"]["runner"]
    if runner == "GEM5" and "TRAFFIC_JSON" not in artifacts:
        raise ContractError(f"{case_id}/{subcase['name']}: GEM5 traffic artifact missing")
    if subcase["terminal_class"] == "EXPECTED_INFRA_FATAL":
        if "FATAL_SNAPSHOT" not in artifacts:
            raise ContractError(f"{case_id}/{subcase['name']}: fatal snapshot missing")
        if subcase["expected_ledgers"]["fatal_records"] != 1:
            raise ContractError(f"{case_id}/{subcase['name']}: fatal_records must be one")
    elif subcase["expected_ledgers"] != ZERO_LEDGERS:
        raise ContractError(f"{case_id}/{subcase['name']}: success ledgers must be zero")
    validate_detail_code(subcase["expected_first_fatal"])
    timeout = subcase["timeout"]
    if runner == "GEM5":
        if canonical_u64(timeout["sim_ticks"]) == 0:
            raise ContractError(f"{case_id}/{subcase['name']}: sim_ticks must be positive")
    elif timeout["sim_ticks"] is not None:
        raise ContractError(f"{case_id}/{subcase['name']}: unit sim_ticks must be null")
    if runner == "GEM5":
        forbidden = (
            "--case",
            "--outdir",
            "--stats",
            "--output",
            "--seed",
            "--master-seed",
            "--timeout",
            "--watchdog",
            "--sim-tick-limit",
            "--listener",
            "--gate",
            "--id",
        )
        for argument in subcase["execution"]["args"]:
            if "\x00" in argument or "\n" in argument or "\r" in argument:
                raise ContractError(f"{case_id}/{subcase['name']}: invalid argument bytes")
            if argument.startswith(forbidden):
                raise ContractError(f"{case_id}/{subcase['name']}: forbidden argument {argument}")


def select_cases(
    document: dict,
    gate: int,
    explicit_ids: list[str] | None = None,
) -> tuple[str, list[dict]]:
    if gate not in CUMULATIVE:
        raise ContractError(f"gate must be one of {tuple(CUMULATIVE)}")
    eligible = [case for case in document["cases"] if case["earliest_gate"] <= gate]
    if explicit_ids is None:
        return "GATE_PREFIX", eligible
    if not explicit_ids or len(explicit_ids) != len(set(explicit_ids)):
        raise ContractError("explicit IDs must be nonempty and unique")
    requested = set(explicit_ids)
    selected = [case for case in eligible if case["id"] in requested]
    if {case["id"] for case in selected} != requested:
        raise ContractError("explicit ID is unknown or not eligible for this gate")
    return "EXPLICIT_IDS", selected


def validate_run_summary(
    document: dict, subcase: dict, root: Path | None = None
) -> None:
    validate_schema("run_summary_v1.schema.json", document)
    validate_detail_code(document["first_fatal"])
    if document["subcase"] != subcase["name"]:
        raise ContractError("run summary subcase identity mismatch")
    if document["terminal_class"] != subcase["terminal_class"]:
        raise ContractError("run summary terminal class mismatch")
    paths = document["artifact_paths"]
    if [row["kind"] for row in paths] != subcase["artifacts"]:
        raise ContractError("run summary artifact kinds mismatch")
    for row in paths:
        if row["state"] == "AVAILABLE":
            if row["path"] != ARTIFACT_BASENAMES[row["kind"]]:
                raise ContractError("available artifact has wrong fixed basename")
            if root is not None and artifact_file_state(
                root / row["path"], root
            ) != "REGULAR":
                raise ContractError("available artifact is not a regular file")
        elif row["path"] is not None or document["status"] != "FAIL":
            raise ContractError("missing artifact state is incoherent")
        if row["kind"] in ("SUMMARY_JSON", "JUNIT_XML") and row["state"] != "AVAILABLE":
            raise ContractError("outer-owned artifact cannot be missing")
    termination = document["child_termination"]
    if termination["kind"] == "EXIT":
        if document["process_exit_code"] != termination["exit_code"]:
            raise ContractError("process exit code and termination differ")
    elif document["process_exit_code"] is not None:
        raise ContractError("non-exit termination has process exit code")
    if document["run_exit_reason"] == "HARNESS_FAILURE":
        if document["harness_failure_kind"] is None:
            raise ContractError("harness failure reason has no failure kind")
        scenario_fields = (
            document["first_fatal"],
            document["watchdog_fired"],
            document["ledger_summary"],
            document["global_quiescence"],
            document["run_manifest_digest"],
        )
        if any(value is not None for value in scenario_fields):
            raise ContractError("harness failure has scenario fields")
    elif any(
        document[name] is None
        for name in (
            "watchdog_fired",
            "ledger_summary",
            "global_quiescence",
            "run_manifest_digest",
        )
    ):
        raise ContractError("scenario result lacks child report fields")
    if document["status"] == "PASS":
        expected_quiescence = subcase["terminal_class"] == "QUIESCENT_SUCCESS"
        if termination["kind"] != "EXIT":
            raise ContractError("passing summary did not exit normally")
        if document["process_exit_code"] != RUN_EXIT_CODES.get(
            document["run_exit_reason"]
        ):
            raise ContractError("passing summary process exit ABI mismatch")
        if document["harness_failure_kind"] is not None:
            raise ContractError("passing summary has harness failure")
        if document["run_exit_reason"] != subcase["expected_exit_reason"]:
            raise ContractError("passing summary exit reason mismatch")
        if document["first_fatal"] != subcase["expected_first_fatal"]:
            raise ContractError("passing summary fatal mismatch")
        if document["ledger_summary"] != subcase["expected_ledgers"]:
            raise ContractError("passing summary ledger mismatch")
        if document["watchdog_fired"]:
            raise ContractError("passing summary fired watchdog")
        if document["global_quiescence"] != expected_quiescence:
            raise ContractError("passing summary quiescence mismatch")
        if document["run_manifest_digest"] is None:
            raise ContractError("passing summary lacks run manifest digest")
        if any(row["state"] != "AVAILABLE" for row in paths):
            raise ContractError("passing summary has missing artifact")


def validate_run_manifest(document: dict) -> None:
    validate_schema("run_manifest_v1.schema.json", document)
    execution = document["execution"]
    environment_rows = execution["child_environment"]
    environment_names = tuple(row["name"] for row in environment_rows)
    if environment_names != CHILD_ENV_KEYS:
        raise ContractError("run manifest child environment is not exact")
    environment = {row["name"]: row["value"] for row in environment_rows}
    if any(environment[name] != value for name, value in CHILD_ENV_BASE.items()):
        raise ContractError("run manifest fixed child environment differs")
    if environment["AI_MESH_CASE_ID"] != document["id"]:
        raise ContractError("run manifest case environment differs")
    if environment["AI_MESH_SUBCASE"] != document["subcase"]:
        raise ContractError("run manifest subcase environment differs")
    output = execution["absolute_output_dir"]
    output_path = Path(output)
    if not output_path.is_absolute() or str(output_path) != output:
        raise ContractError("run manifest output directory is not normalized absolute")
    if environment["AI_MESH_ARTIFACT_DIR"] != output:
        raise ContractError("run manifest artifact environment differs")
    if environment["AI_MESH_CHILD_REPORT"] != str(output_path / "child_report.json"):
        raise ContractError("run manifest child report environment differs")
    inherited = document["provenance"]["inherited_environment"]
    inherited_names = [row["name"] for row in inherited]
    if inherited_names != sorted(set(inherited_names), key=lambda value: value.encode("utf-8")):
        raise ContractError("inherited environment is not unique canonical order")
    identity = {
        "runner": execution["runner"],
        "resolved_executable": execution["final_argv"][0],
        "final_argv": execution["final_argv"],
        "child_environment": environment_rows,
        "inherited_environment_digest": canonical_digest(inherited),
    }
    if execution["normalized_execution_digest"] != canonical_digest(identity):
        raise ContractError("normalized execution digest mismatch")
    scenario = document["scenario"]
    if execution["runner"] == "GEM5":
        if scenario["kind"] != "GEM5":
            raise ContractError("GEM5 runner has unit scenario")
        canonical_u64(scenario["master_seed"])
        programs = scenario["mesh_programs"]
        program_ids = [row["program_id"] for row in programs]
        if program_ids != sorted(set(program_ids)) or any(value == 0 for value in program_ids):
            raise ContractError("mesh programs are not unique numeric order")
        counters = scenario["physical_source_counters"]
        counter_keys = [
            (
                row["kind"],
                row["component_kind"],
                row["component_local_id"],
                row["endpoint_id"],
            )
            for row in counters
        ]
        if counter_keys != sorted(set(counter_keys)):
            raise ContractError("physical source counters are not unique order")
        tick_projection = scenario["tick_projection"]
        for name in (
            "host_clock_period_ticks",
            "npu_clock_period_ticks",
            "core_clock_period_ticks",
        ):
            if canonical_u64(tick_projection[name]) == 0:
                raise ContractError("clock period projection must be positive")
        host_ids = [row["host_task_id"] for row in tick_projection["host_tasks"]]
        if host_ids != sorted(set(host_ids)):
            raise ContractError("host task ticks are not unique numeric order")
    else:
        if scenario["kind"] != "UNIT":
            raise ContractError("unit runner has GEM5 scenario")
        if scenario["unit_registry_digest"] != document["provenance"][
            "config_or_test_registry_sha256"
        ]:
            raise ContractError("unit registry provenance mismatch")


def validate_results(
    document: dict,
    manifest: dict,
    selected_cases: list[dict],
    root: Path | None = None,
) -> None:
    validate_schema("mandatory_results_v1.schema.json", document)
    manifest_digest = validate_manifest(manifest)
    if document["manifest_digest"] != manifest_digest:
        raise ContractError("results manifest digest mismatch")
    selected_ids = [case["id"] for case in selected_cases]
    if document["selection"]["ids"] != selected_ids:
        raise ContractError("results selection IDs mismatch")
    if any(case["earliest_gate"] > document["gate"] for case in selected_cases):
        raise ContractError("results include a case before its gate")
    if document["selection"]["mode"] == "GATE_PREFIX":
        expected_ids = [
            case["id"]
            for case in manifest["cases"]
            if case["earliest_gate"] <= document["gate"]
        ]
        if selected_ids != expected_ids:
            raise ContractError("gate-prefix selection is incomplete")
    result_cases = document["cases"]
    if [case["id"] for case in result_cases] != selected_ids:
        raise ContractError("result case order differs from selection")
    subcase_pass = 0
    subcase_fail = 0
    logical_pass = 0
    logical_fail = 0
    for expected_case, result_case in zip(selected_cases, result_cases):
        expected_names = [subcase["name"] for subcase in expected_case["subcases"]]
        result_subcases = result_case["subcases"]
        if [subcase["name"] for subcase in result_subcases] != expected_names:
            raise ContractError("result subcases differ from manifest")
        for expected_subcase, result_subcase in zip(
            expected_case["subcases"], result_subcases
        ):
            name = result_subcase["name"]
            if result_subcase["summary_path"] != f"{result_case['id']}/{name}/summary.json":
                raise ContractError("result summary path mismatch")
            if result_subcase["junit_testcase_name"] != f"{result_case['id']}/{name}":
                raise ContractError("result JUnit identity mismatch")
            if root is not None:
                artifact_dir = (root.resolve() / result_case["id"] / name).resolve()
                summary = read_json_artifact(
                    artifact_dir / "summary.json",
                    root,
                    "run_summary_v1.schema.json",
                )
                validate_run_summary(summary, expected_subcase, artifact_dir)
                if summary["id"] != result_case["id"]:
                    raise ContractError("result summary case identity mismatch")
                if summary["status"] != result_subcase["status"]:
                    raise ContractError("result and summary status mismatch")
                if result_subcase["status"] == "PASS":
                    run_manifest = read_json_artifact(
                        artifact_dir / "run_manifest.json",
                        artifact_dir,
                        "run_manifest_v1.schema.json",
                    )
                    validate_run_manifest(run_manifest)
                    if (
                        run_manifest["id"] != result_case["id"]
                        or run_manifest["subcase"] != name
                        or run_manifest["manifest_digest"] != manifest_digest
                    ):
                        raise ContractError("result RunManifest identity mismatch")
                    if canonical_digest(run_manifest) != summary["run_manifest_digest"]:
                        raise ContractError("result RunManifest digest mismatch")
                    if (
                        run_manifest["execution"]["normalized_execution_digest"]
                        != summary["normalized_execution_digest"]
                    ):
                        raise ContractError("result execution digest mismatch")
                validate_junit(
                    artifact_dir / "junit.xml",
                    artifact_dir,
                    [(result_case["id"], name, result_subcase["status"])],
                )
            if result_subcase["status"] == "PASS":
                subcase_pass += 1
            else:
                subcase_fail += 1
        expected_status = (
            "PASS"
            if all(subcase["status"] == "PASS" for subcase in result_subcases)
            else "FAIL"
        )
        if result_case["status"] != expected_status:
            raise ContractError("logical result status mismatch")
        if expected_status == "PASS":
            logical_pass += 1
        else:
            logical_fail += 1
    expected_counts = {
        "selected_logical_count": len(result_cases),
        "selected_subcase_count": subcase_pass + subcase_fail,
        "logical_pass_count": logical_pass,
        "logical_fail_count": logical_fail,
        "subcase_pass_count": subcase_pass,
        "subcase_fail_count": subcase_fail,
    }
    for name, expected in expected_counts.items():
        if document[name] != expected:
            raise ContractError(f"results {name} mismatch")
    if document["junit_path"] != f"junit_gate{document['gate']}.xml":
        raise ContractError("results JUnit path mismatch")


def validate_junit(
    path: Path,
    root: Path,
    expected: list[tuple[str, str, str]],
) -> None:
    try:
        document = ET.fromstring(read_regular_bytes_once(path, root))
    except ET.ParseError as error:
        raise ContractError(f"invalid JUnit XML: {error}") from error
    if document.tag != "testsuites":
        raise ContractError("JUnit root must be testsuites")
    failures = sum(status == "FAIL" for unused_id, unused_name, status in expected)
    if document.get("tests") != str(len(expected)):
        raise ContractError("JUnit testcase count mismatch")
    if document.get("failures") != str(failures):
        raise ContractError("JUnit failure count mismatch")
    grouped = []
    for case_id, name, status in expected:
        if not grouped or grouped[-1][0] != case_id:
            grouped.append((case_id, []))
        grouped[-1][1].append((name, status))
    suites = list(document)
    if len(suites) != len(grouped) or any(suite.tag != "testsuite" for suite in suites):
        raise ContractError("JUnit suite set mismatch")
    for suite, (case_id, subcases) in zip(suites, grouped):
        expected_failures = sum(status == "FAIL" for unused_name, status in subcases)
        if suite.get("name") != case_id:
            raise ContractError("JUnit suite identity mismatch")
        if suite.get("tests") != str(len(subcases)):
            raise ContractError("JUnit suite count mismatch")
        if suite.get("failures") != str(expected_failures):
            raise ContractError("JUnit suite failure count mismatch")
        testcases = list(suite)
        if len(testcases) != len(subcases) or any(
            testcase.tag != "testcase" for testcase in testcases
        ):
            raise ContractError("JUnit testcase set mismatch")
        for testcase, (name, status) in zip(testcases, subcases):
            if testcase.get("classname") != case_id:
                raise ContractError("JUnit testcase class mismatch")
            if testcase.get("name") != f"{case_id}/{name}":
                raise ContractError("JUnit testcase identity mismatch")
            children = list(testcase)
            if status == "PASS" and children:
                raise ContractError("passing JUnit testcase has child outcome")
            if status == "FAIL" and (
                len(children) != 1 or children[0].tag != "failure"
            ):
                raise ContractError("failing JUnit testcase lacks one failure")


def ensure_output_directory(root: Path, case_id: str, subcase: str) -> Path:
    absolute_root = root.resolve()
    target = (absolute_root / case_id / subcase).resolve()
    if target == absolute_root or absolute_root not in target.parents:
        raise ContractError("subcase output directory escapes suite root")
    target.mkdir(parents=True, exist_ok=False)
    return target


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, document: dict) -> None:
    atomic_write_bytes(path, canonical_json_bytes(document) + b"\n")


def read_regular_bytes_once(path: Path, root: Path) -> bytes:
    absolute_root = root.resolve()
    resolved_parent = path.parent.resolve()
    if resolved_parent != absolute_root and absolute_root not in resolved_parent.parents:
        raise ContractError(f"artifact path escapes root: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ContractError(f"cannot open regular artifact {path.name}: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContractError(f"artifact is not a regular file: {path.name}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_json_artifact(path: Path, root: Path, schema_filename: str) -> dict:
    try:
        document = json.loads(read_regular_bytes_once(path, root))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"invalid JSON artifact {path.name}: {error}") from error
    validate_schema(schema_filename, document)
    return document


def artifact_file_state(path: Path, root: Path) -> str:
    absolute_root = root.resolve()
    resolved_parent = path.parent.resolve()
    if resolved_parent != absolute_root and absolute_root not in resolved_parent.parents:
        raise ContractError(f"artifact path escapes root: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "MISSING"
    return "REGULAR" if stat.S_ISREG(metadata.st_mode) else "INVALID"


def validate_invariants(
    document: dict,
    case_id: str,
    subcase: str,
    expected_names: tuple[str, ...] | list[str] | None = None,
) -> None:
    if document["id"] != case_id or document["subcase"] != subcase:
        raise ContractError("invariant artifact identity mismatch")
    names = [check["name"] for check in document["checks"]]
    if len(names) != len(set(names)):
        raise ContractError("duplicate invariant check")
    if expected_names is not None and names != list(expected_names):
        raise ContractError("invariant registry does not match selected execution")
    if document["registry_digest"] != canonical_digest(names):
        raise ContractError("invariant registry digest mismatch")
    failures = [check["name"] for check in document["checks"] if check["status"] == "FAIL"]
    for check in document["checks"]:
        if check["expected"]["kind"] != check["observed"]["kind"]:
            raise ContractError("invariant value kind mismatch")
        equal = check["expected"]["value"] == check["observed"]["value"]
        if (check["status"] == "PASS") != equal:
            raise ContractError("invariant status/value mismatch")
    expected_status = "FAIL" if failures else "PASS"
    expected_first = failures[0] if failures else None
    if document["status"] != expected_status or document["first_failure"] != expected_first:
        raise ContractError("invariant aggregate mismatch")


def _traffic_projections(classes: list[dict], ownership: list[dict]) -> tuple[dict, dict]:
    expected = {
        "classes": [
            [row["traffic_class"], row["expected_bytes"], row["expected_packets"]]
            for row in classes
        ],
        "ownership": [
            [row["owner_key_wire"], row["expected_bytes"]] for row in ownership
        ],
    }
    actual = {
        "classes": [
            [row["traffic_class"], row["actual_bytes"], row["actual_packets"]]
            for row in classes
        ],
        "ownership": [
            [row["owner_key_wire"], row["actual_bytes"]] for row in ownership
        ],
    }
    return expected, actual


def validate_traffic(document: dict, case_id: str, subcase: str) -> None:
    if document["id"] != case_id or document["subcase"] != subcase:
        raise ContractError("traffic artifact identity mismatch")
    classes = [row["traffic_class"] for row in document["classes"]]
    class_order = (
        "AGENT_TO_NPU_CONTROL",
        "NPU_TO_AGENT_CONTROL",
        "NPU_READ_AGENT_MEMORY",
        "NPU_WRITE_AGENT_MEMORY",
        "NPU_LOCAL_MEMORY",
        "NPU_P2P",
    )
    if classes != sorted(set(classes), key=class_order.index):
        raise ContractError("traffic classes are not unique canonical order")
    owners = [row["owner_key_wire"] for row in document["ownership"]]
    if owners != sorted(set(owners)):
        raise ContractError("traffic ownership is not unique canonical order")
    expected_projection, actual_projection = _traffic_projections(
        document["classes"], document["ownership"]
    )
    if document["oracle_digest"] != canonical_digest(expected_projection):
        raise ContractError("traffic oracle digest mismatch")
    if document["actual_digest"] != canonical_digest(actual_projection):
        raise ContractError("traffic actual digest mismatch")
    matched = document["oracle_digest"] == document["actual_digest"]
    matched = matched and canonical_u64(document["unattributed_bytes"]) == 0
    matched = matched and all(
        canonical_u64(row["expected_bytes"]) == canonical_u64(row["actual_bytes"])
        and canonical_u64(row["expected_packets"]) == canonical_u64(row["actual_packets"])
        for row in document["classes"]
    )
    matched = matched and all(
        canonical_u64(row["expected_bytes"]) == canonical_u64(row["actual_bytes"])
        for row in document["ownership"]
    )
    if document["status"] != ("PASS" if matched else "FAIL"):
        raise ContractError("traffic aggregate mismatch")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def acceptance_environment() -> tuple[str, str, Path, Path]:
    required = (
        "AI_MESH_CASE_ID",
        "AI_MESH_SUBCASE",
        "AI_MESH_ARTIFACT_DIR",
        "AI_MESH_CHILD_REPORT",
    )
    if any(name not in os.environ for name in required):
        raise ContractError("acceptance environment is incomplete")
    case_id = os.environ["AI_MESH_CASE_ID"]
    subcase = os.environ["AI_MESH_SUBCASE"]
    artifact_dir = Path(os.environ["AI_MESH_ARTIFACT_DIR"]).resolve()
    report_path = Path(os.environ["AI_MESH_CHILD_REPORT"])
    if report_path.resolve() != artifact_dir / "child_report.json":
        raise ContractError("child report path does not match artifact directory")
    return case_id, subcase, artifact_dir, report_path


def write_success_artifacts(invariant_names: list[str], traffic: dict | None = None) -> None:
    case_id, subcase, artifact_dir, report_path = acceptance_environment()
    run_manifest_path = artifact_dir / ARTIFACT_BASENAMES["RUN_MANIFEST"]
    run_manifest = read_json_artifact(
        run_manifest_path, artifact_dir, "run_manifest_v1.schema.json"
    )
    validate_run_manifest(run_manifest)
    if run_manifest["id"] != case_id or run_manifest["subcase"] != subcase:
        raise ContractError("run manifest identity mismatch")
    run_manifest_digest = canonical_digest(run_manifest)
    names = list(invariant_names)
    if not names or len(names) != len(set(names)):
        raise ContractError("invariant registry must be nonempty and unique")
    checks = [
        {
            "name": name,
            "status": "PASS",
            "expected": {"kind": "BOOL", "value": True},
            "observed": {"kind": "BOOL", "value": True},
        }
        for name in names
    ]
    invariants = {
        "schema": "ai_mesh_invariants_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "PASS",
        "registry_digest": canonical_digest(names),
        "checks": checks,
        "first_failure": None,
    }
    validate_schema("invariants_v1.schema.json", invariants)
    validate_invariants(invariants, case_id, subcase)
    atomic_write_json(
        artifact_dir / ARTIFACT_BASENAMES["INVARIANTS_JSON"], invariants
    )
    if traffic is not None:
        validate_schema("traffic_v1.schema.json", traffic)
        validate_traffic(traffic, case_id, subcase)
        atomic_write_json(
            artifact_dir / ARTIFACT_BASENAMES["TRAFFIC_JSON"], traffic
        )
    child_report = {
        "schema": "ai_mesh_child_scenario_report_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "run_exit_reason": "QUIESCENT_SUCCESS",
        "first_fatal": None,
        "watchdog_fired": False,
        "ledger_summary": dict(ZERO_LEDGERS),
        "global_quiescence": True,
        "run_manifest_digest": run_manifest_digest,
    }
    validate_schema("child_scenario_report_v1.schema.json", child_report)
    atomic_write_json(report_path, child_report)


def build_dma_traffic(
    case_id: str,
    subcase: str,
    expected_rows: list[dict],
    actual_rows: list[dict],
    scale: int,
) -> dict:
    if isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0:
        raise ContractError("traffic scale must be a positive integer")
    expected_by_id = {row["descriptor_id"]: row for row in expected_rows}
    actual_by_id = {row["descriptor_id"]: row for row in actual_rows}
    if len(expected_by_id) != len(expected_rows):
        raise ContractError("duplicate expected traffic descriptor")
    if len(actual_by_id) != len(actual_rows):
        raise ContractError("duplicate actual traffic descriptor")
    ownership = []
    aggregates = {
        "NPU_LOCAL_MEMORY": [0, 0, 0, 0],
        "NPU_P2P": [0, 0, 0, 0],
    }
    applicable = set()
    for descriptor_id in sorted(set(expected_by_id) | set(actual_by_id)):
        expected = expected_by_id.get(descriptor_id, {})
        actual = actual_by_id.get(descriptor_id, {})
        kind = expected.get("kind")
        expected_bytes = int(expected.get("useful_bytes", 0)) * scale
        expected_packets = int(expected.get("bursts", 0)) * scale
        if kind in (mesh_abi.DMA_KIND.LOAD, mesh_abi.DMA_KIND.PREFETCH):
            actual_bytes = int(actual.get("read_bytes", 0)) + int(
                actual.get("read_discarded_bytes", 0)
            )
            actual_packets = int(actual.get("read_bursts", 0))
        elif kind == mesh_abi.DMA_KIND.STORE:
            actual_bytes = int(actual.get("write_bytes", 0)) + int(
                actual.get("write_drained_uncommitted_bytes", 0)
            )
            actual_packets = int(actual.get("write_bursts", 0))
        elif kind == mesh_abi.DMA_KIND.P2P_PUSH:
            actual_bytes = int(actual.get("p2p_bytes", 0))
            actual_packets = int(actual.get("p2p_bursts", 0))
        elif kind == mesh_abi.DMA_KIND.LOCAL_FILL:
            actual_bytes = int(actual.get("fill_bytes", 0))
            actual_packets = 0
            expected_packets = 0
        else:
            actual_bytes = sum(
                int(actual.get(name, 0))
                for name in ("read_bytes", "write_bytes", "p2p_bytes", "fill_bytes")
            )
            actual_packets = sum(
                int(actual.get(name, 0))
                for name in ("read_bursts", "write_bursts", "p2p_bursts")
            )
        traffic_class = (
            "NPU_P2P"
            if kind == mesh_abi.DMA_KIND.P2P_PUSH
            else "NPU_LOCAL_MEMORY"
        )
        applicable.add(traffic_class)
        totals = aggregates[traffic_class]
        totals[0] += expected_bytes
        totals[1] += actual_bytes
        totals[2] += expected_packets
        totals[3] += actual_packets
        ownership.append(
            {
                "owner_key_wire": f"{descriptor_id:08x}",
                "expected_bytes": expected_bytes,
                "actual_bytes": actual_bytes,
            }
        )
    classes = []
    for traffic_class in ("NPU_LOCAL_MEMORY", "NPU_P2P"):
        expected_bytes, actual_bytes, expected_packets, actual_packets = aggregates[
            traffic_class
        ]
        if traffic_class in applicable:
            classes.append(
                {
                    "traffic_class": traffic_class,
                    "expected_bytes": expected_bytes,
                    "actual_bytes": actual_bytes,
                    "expected_packets": expected_packets,
                    "actual_packets": actual_packets,
                }
            )
    expected_projection, actual_projection = _traffic_projections(classes, ownership)
    oracle_digest = canonical_digest(expected_projection)
    actual_digest = canonical_digest(actual_projection)
    matched = oracle_digest == actual_digest
    return {
        "schema": "ai_mesh_traffic_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase,
        "status": "PASS" if matched else "FAIL",
        "oracle_digest": oracle_digest,
        "actual_digest": actual_digest,
        "classes": classes,
        "ownership": ownership,
        "unattributed_bytes": 0,
    }
