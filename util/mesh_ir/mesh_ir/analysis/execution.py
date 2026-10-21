from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Callable, TypeVar

from mesh_ir.canonical import to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor, PassTask


T = TypeVar('T')
R = TypeVar('R')


@dataclass(frozen=True)
class AnalysisStage:
    name: str
    elapsed_ns: int
    status: str


class AnalysisExecution:
    def __init__(self, workers: int = 1):
        self._executor = PassExecutor(workers)
        self._token: Token[AnalysisExecution | None] | None = None
        self._stages: tuple[AnalysisStage, ...] = ()

    @property
    def workers(self) -> int:
        return self._executor.workers

    @property
    def diagnostics(self) -> ExecutionDiagnostics:
        return self._executor.diagnostics

    def canonical_dict(self) -> dict:
        return dict(execution=to_canonical(self.diagnostics), stages=to_canonical(self._stages))

    def __enter__(self) -> AnalysisExecution:
        if _CURRENT.get() is not None or self._token is not None:
            raise MeshIrError('E_CONFIG', 'analysis execution scope is already active')
        self._executor.__enter__()
        self._token = _CURRENT.set(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._token is None:
            raise MeshIrError('E_CONFIG', 'analysis execution scope is not active')
        _CURRENT.reset(self._token)
        self._token = None
        self._executor.__exit__(exc_type, exc_value, traceback)

    def run(self, pass_name: str, function: Callable[[T], R], tasks: tuple[PassTask[T], ...]) -> tuple[R, ...]:
        if _CURRENT.get() is not self:
            raise MeshIrError('E_CONFIG', 'analysis execution scope is not active')
        return tuple(result.value for result in self._executor.run(pass_name, function, tasks))

    @contextmanager
    def stage(self, name: str):
        logger = logging.getLogger(__name__)
        started = time.perf_counter_ns()
        status = 'FAIL'
        logger.info('analysis stage start name=%s workers=%d', name, self.workers)
        try:
            yield
            status = 'PASS'
        finally:
            elapsed = time.perf_counter_ns() - started
            self._stages += (AnalysisStage(name, elapsed, status),)
            logger.info('analysis stage complete name=%s status=%s elapsed_seconds=%.3f', name, status, elapsed / 1e9)


_CURRENT: ContextVar[AnalysisExecution | None] = ContextVar('mesh_analysis_execution', default=None)


def current_execution() -> AnalysisExecution | None:
    return _CURRENT.get()


@contextmanager
def analysis_stage(name: str):
    execution = current_execution()
    if execution is None:
        yield
    else:
        with execution.stage(name):
            yield
