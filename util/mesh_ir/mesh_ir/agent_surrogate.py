from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mesh_ir.agent_workload import (
    PlanError,
    WorkloadPlan,
    load_strict_json,
    u64_value,
    validate_against_schema,
)
from mesh_ir.model import canonical_json_bytes


@dataclass(frozen=True)
class SurrogateProfile:
    program_id: int
    profile_id: int
    profile_key: int
    input_tokens: int
    input_bytes: int
    output_tokens: int
    output_bytes: int
    service_ns: int
    publish_chunk_bytes: int


@dataclass(frozen=True)
class SurrogateProfileRegistry:
    profiles: tuple[SurrogateProfile, ...]
    digest: str
    document: dict

    def find(self, program_id: int, profile_id: int) -> SurrogateProfile | None:
        for profile in self.profiles:
            if profile.program_id == program_id and profile.profile_id == profile_id:
                return profile
        return None


def _semantic(document) -> None:
    seen = []
    for index, record in enumerate(document["profiles"]):
        key = (record["program_id"], record["profile_id"])
        if key in seen:
            raise PlanError(
                "E_AGENT_PLAN",
                f"profiles/{index}",
                f"duplicate profile identity {key}",
            )
        seen.append(key)
    if seen != sorted(seen):
        raise PlanError(
            "E_AGENT_PLAN",
            "profiles",
            "profiles must be strictly ascending by program_id then profile_id",
        )
    chunks = {record["publish_chunk_bytes"] for record in document["profiles"]}
    if len(chunks) != 1:
        raise PlanError(
            "E_AGENT_PLAN",
            "profiles",
            "publish_chunk_bytes must be identical across the registry",
        )


def load_surrogate_profiles(path) -> SurrogateProfileRegistry:
    document = load_strict_json(path)
    validate_against_schema("agent_surrogate_profiles_v1.schema.json", document)
    _semantic(document)
    profiles = tuple(
        SurrogateProfile(
            program_id=record["program_id"],
            profile_id=record["profile_id"],
            profile_key=u64_value(record["profile_key"]),
            input_tokens=record["input_tokens"],
            input_bytes=u64_value(record["input_bytes"]),
            output_tokens=record["output_tokens"],
            output_bytes=u64_value(record["output_bytes"]),
            service_ns=u64_value(record["service_ns"]),
            publish_chunk_bytes=record["publish_chunk_bytes"],
        )
        for record in document["profiles"]
    )
    return SurrogateProfileRegistry(
        profiles=profiles,
        digest=hashlib.sha256(canonical_json_bytes(document)).hexdigest(),
        document=document,
    )


def validate_surrogate_profiles(
    registry: SurrogateProfileRegistry, workload: WorkloadPlan
) -> None:
    for user in workload.users:
        for task in user.tasks:
            for round_ in task.rounds:
                base = f"users/{user.user_id}/tasks/{task.task_seq}/rounds/{round_.repair_round}"
                profile = registry.find(round_.program_id, round_.profile_id)
                if profile is None:
                    raise PlanError(
                        "E_WORKLOAD_PLAN_MISMATCH",
                        f"{base}/profile_id",
                        "no surrogate profile for the planned request",
                    )
                if profile.profile_key != round_.requested_profile_key:
                    raise PlanError(
                        "E_WORKLOAD_PLAN_MISMATCH",
                        f"{base}/requested_profile_key",
                        "profile key differs from the surrogate registry",
                    )
                if profile.input_tokens != round_.full_context_tokens:
                    raise PlanError(
                        "E_WORKLOAD_PLAN_MISMATCH",
                        f"{base}/full_context_tokens",
                        "surrogate profile input tokens differ from the plan",
                    )
                if profile.input_bytes != round_.full_context_bytes:
                    raise PlanError(
                        "E_WORKLOAD_PLAN_MISMATCH",
                        f"{base}/full_context_bytes",
                        "surrogate profile input bytes differ from the plan",
                    )
                if profile.output_tokens != round_.output_tokens:
                    raise PlanError(
                        "E_WORKLOAD_PLAN_MISMATCH",
                        f"{base}/output_tokens",
                        "surrogate profile output tokens differ from the plan",
                    )
                if profile.output_bytes != round_.generated_code_bytes:
                    raise PlanError(
                        "E_WORKLOAD_PLAN_MISMATCH",
                        f"{base}/generated_code_bytes",
                        "surrogate profile output bytes differ from the plan",
                    )
