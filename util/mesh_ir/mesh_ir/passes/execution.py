from __future__ import annotations

import multiprocessing
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from mesh_ir.canonical import checked_u64
from mesh_ir.diagnostics import MeshIrError


T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class PassDefinition:
    name: str
    version: int


@dataclass(frozen=True)
class PassRecord:
    name: str
    version: int
    input_hash: str
    output_hash: str
    elapsed_ns: int
    statistics: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class PassTask(Generic[T]):
    task_id: str
    payload: T


@dataclass(frozen=True)
class CompletedTask(Generic[R]):
    task_id: str
    value: R


@dataclass(frozen=True)
class TaskDiagnostic:
    pass_name: str
    task_id: str
    submission_index: int
    completion_index: int
    worker_pid: int | None
    elapsed_ns: int | None


@dataclass(frozen=True)
class ExecutionDiagnostics:
    workers: int
    submitted_tasks: int
    completed_tasks: int
    tasks: tuple[TaskDiagnostic, ...]


@dataclass(frozen=True)
class _WorkerSuccess(Generic[R]):
    task_id: str
    value: R
    worker_pid: int
    elapsed_ns: int


@dataclass(frozen=True)
class _WorkerFailure:
    task_id: str
    code: str
    message: str
    context: dict[str, object]
    worker_pid: int
    elapsed_ns: int


PASS_REGISTRY = tuple(
    PassDefinition(name, 1)
    for name in (
        "LoadAndValidateExport",
        "DecomposeToPinnedCoreAten",
        "ImportGraphIR",
        "CanonicalizeFunctionalOps",
        "SpecializeShapeProfiles",
        "FuseVerifiedPatterns",
        "AnnotateCostAndBytes",
        "ChooseParallelPlan",
        "PlaceOpsAndTensors",
        "ShardAndPadTensors",
        "TileKernels",
        "BufferizeAndAlias",
        "LowerCollectives",
        "InsertDataMovement",
        "PlanStaticSRAM",
        "InsertHazardDependencies",
        "SchedulePerCoreStreams",
        "LowerDmaToSegments",
        "BindAddressesAndRelocations",
        "VerifyScheduledIR",
        "ComputeExpectedTraffic",
        "EncodeArtifacts",
    )
)


_PASS_BY_NAME = {item.name: item for item in PASS_REGISTRY}
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _pass_definition(name: str) -> PassDefinition:
    if type(name) is not str or name not in _PASS_BY_NAME:
        raise MeshIrError("E_CONFIG", "compiler pass is not registered", pass_name=name)
    return _PASS_BY_NAME[name]


def _validate_digest(value: str, field: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise MeshIrError("E_ABI_CHECKSUM", "compiler pass hash is malformed", field=field, value=value)


def record_pass(
    name: str,
    input_hash: str,
    output_hash: str,
    elapsed_ns: int,
    statistics: tuple[tuple[str, int], ...] = (),
) -> PassRecord:
    definition = _pass_definition(name)
    _validate_digest(input_hash, "input_hash")
    _validate_digest(output_hash, "output_hash")
    checked_u64(elapsed_ns, "elapsed_ns")
    if type(statistics) is not tuple:
        raise MeshIrError("E_CONFIG", "compiler pass statistics must be an immutable tuple", pass_name=name)
    normalized = []
    seen = set()
    for item in statistics:
        if type(item) is not tuple or len(item) != 2:
            raise MeshIrError("E_CONFIG", "compiler pass statistic is malformed", pass_name=name)
        key, value = item
        if type(key) is not str or not key or key in seen:
            raise MeshIrError("E_CONFIG", "compiler pass statistic name is invalid or duplicated", pass_name=name, statistic=key)
        checked_u64(value, key)
        seen.add(key)
        normalized.append((key, value))
    return PassRecord(name, definition.version, input_hash, output_hash, elapsed_ns, tuple(sorted(normalized)))


def verify_pass_chain(
    records: tuple[PassRecord, ...],
    initial_hash: str,
    final_hash: str,
    expected_names: tuple[str, ...],
) -> None:
    _validate_digest(initial_hash, "initial_hash")
    _validate_digest(final_hash, "final_hash")
    if type(records) is not tuple or type(expected_names) is not tuple or any(type(name) is not str for name in expected_names):
        raise MeshIrError("E_CONFIG", "compiler pass chain must use immutable typed records")
    registry_names = tuple(item.name for item in PASS_REGISTRY)
    if expected_names:
        try:
            offset = registry_names.index(expected_names[0])
        except ValueError as error:
            raise MeshIrError("E_CONFIG", "compiler pass chain contains an unregistered pass") from error
        if registry_names[offset:offset + len(expected_names)] != expected_names:
            raise MeshIrError("E_CONFIG", "compiler pass chain order is invalid")
    if len(records) != len(expected_names):
        raise MeshIrError("E_CONFIG", "compiler pass chain length is invalid")
    previous = initial_hash
    for record, expected_name in zip(records, expected_names):
        if type(record) is not PassRecord:
            raise MeshIrError("E_CONFIG", "compiler pass chain record type is invalid")
        validated = record_pass(record.name, record.input_hash, record.output_hash, record.elapsed_ns, record.statistics)
        if type(record.version) is not int or validated != record or record.name != expected_name or record.input_hash != previous:
            raise MeshIrError("E_CONFIG", "compiler pass chain is invalid", pass_name=record.name)
        previous = record.output_hash
    if previous != final_hash:
        raise MeshIrError("E_CONFIG", "compiler pass chain final hash is invalid")


def _execute_task(task_function: Callable[[T], R], task: PassTask[T]) -> _WorkerSuccess[R] | _WorkerFailure:
    started = time.perf_counter_ns()
    try:
        value = task_function(task.payload)
    except MeshIrError as error:
        return _WorkerFailure(task.task_id, error.code, error.message, error.context, os.getpid(), time.perf_counter_ns() - started)
    return _WorkerSuccess(task.task_id, value, os.getpid(), time.perf_counter_ns() - started)


class PassExecutor:
    def __init__(self, workers: int):
        if type(workers) is not int or workers <= 0:
            raise MeshIrError("E_CONFIG", "worker count must be a positive integer", workers=workers)
        self._workers = workers
        self._pool: ProcessPoolExecutor | None = None
        self._tasks: tuple[TaskDiagnostic, ...] = ()
        self._submitted = 0
        self._completed = 0

    @property
    def workers(self) -> int:
        return self._workers

    @property
    def diagnostics(self) -> ExecutionDiagnostics:
        return ExecutionDiagnostics(self._workers, self._submitted, self._completed, self._tasks)

    def __enter__(self) -> PassExecutor:
        if self._pool is not None:
            raise MeshIrError("E_CONFIG", "pass executor is already active")
        self._pool = ProcessPoolExecutor(max_workers=self._workers, mp_context=multiprocessing.get_context("spawn"))
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=False)

    def run(
        self,
        pass_name: str,
        task_function: Callable[[T], R],
        tasks: tuple[PassTask[T], ...],
    ) -> tuple[CompletedTask[R], ...]:
        _pass_definition(pass_name)
        if self._pool is None:
            raise MeshIrError("E_CONFIG", "pass executor is not active")
        if type(tasks) is not tuple or not callable(task_function):
            raise MeshIrError("E_CONFIG", "pass task batch is malformed", pass_name=pass_name)
        task_ids = []
        for task in tasks:
            if type(task) is not PassTask or type(task.task_id) is not str or not task.task_id:
                raise MeshIrError("E_CONFIG", "pass task identity is invalid", pass_name=pass_name)
            task_ids.append(task.task_id)
        if len(set(task_ids)) != len(task_ids):
            raise MeshIrError("E_CONFIG", "pass task identities must be unique", pass_name=pass_name)
        submission_base = self._submitted
        futures = {self._pool.submit(_execute_task, task_function, task): index for index, task in enumerate(tasks)}
        self._submitted += len(tasks)
        responses: dict[int, _WorkerSuccess[R] | _WorkerFailure] = {}
        failures: dict[int, Exception] = {}
        completion_order: dict[int, int] = {}
        for completion_index, future in enumerate(as_completed(futures)):
            index = futures[future]
            try:
                response = future.result()
            except Exception as error:
                failures[index] = error
            else:
                responses[index] = response
                if isinstance(response, _WorkerFailure):
                    failures[index] = MeshIrError(response.code, response.message, **response.context)
            completion_order[index] = completion_index
        diagnostics = []
        for index, task in enumerate(tasks):
            response = responses.get(index)
            diagnostics.append(TaskDiagnostic(
                pass_name,
                task.task_id,
                submission_base + index,
                self._completed + completion_order[index],
                None if response is None else response.worker_pid,
                None if response is None else response.elapsed_ns,
            ))
        self._tasks += tuple(diagnostics)
        self._completed += len(tasks)
        if failures:
            raise failures[min(failures)]
        return tuple(CompletedTask(responses[index].task_id, responses[index].value) for index in range(len(tasks)))


__all__ = [
    "CompletedTask",
    "ExecutionDiagnostics",
    "PASS_REGISTRY",
    "PassDefinition",
    "PassExecutor",
    "PassRecord",
    "PassTask",
    "TaskDiagnostic",
    "record_pass",
    "verify_pass_chain",
]
