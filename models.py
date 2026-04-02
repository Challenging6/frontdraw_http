from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


@dataclass(frozen=True)
class TrialCreateRequest:
    trial_hash: str
    image: str
    task_id: str
    technology: str
    condition: str
    bundle_profile: str
    timeout_sec: int

    def to_payload(self) -> Dict[str, Any]:
        return {
            "trial_hash": self.trial_hash,
            "image": self.image,
            "task_id": self.task_id,
            "technology": self.technology,
            "condition": self.condition,
            "bundle_profile": self.bundle_profile,
            "timeout_sec": self.timeout_sec,
        }


@dataclass(frozen=True)
class TrialCreateResponse:
    trial_id: str
    sandbox_id: str
    workspace_root: str
    image: str
    status: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class DataRef:
    target_path: str
    content_ref: str

    def to_payload(self) -> Dict[str, str]:
        return {
            "target_path": self.target_path,
            "content_ref": self.content_ref,
        }


@dataclass(frozen=True)
class PrepareRefsRequest:
    instruction_text: str
    task_toml_text: str
    task_assets_ref: str | None = None
    data_refs: Sequence[DataRef] = ()
    skills_ref: str | None = None
    agent_home_profiles: Sequence[str] = ("codex",)

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "instruction_text": self.instruction_text,
            "task_toml_text": self.task_toml_text,
            "agent_home_profiles": list(self.agent_home_profiles),
        }
        if self.task_assets_ref:
            payload["task_assets_ref"] = self.task_assets_ref
        if self.data_refs:
            payload["data_refs"] = [item.to_payload() for item in self.data_refs]
        if self.skills_ref:
            payload["skills_ref"] = self.skills_ref
        return payload


@dataclass(frozen=True)
class PrepareTarballRequest:
    tarball_url: str

    def to_payload(self) -> Dict[str, Any]:
        return {
            "upload_mode": "tarball",
            "tarball_url": self.tarball_url,
        }


@dataclass(frozen=True)
class PrepareResponse:
    trial_id: str
    prepared: bool
    workspace_root: str
    written_paths: Sequence[str]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ExecRequest:
    cmd: str
    cwd: str
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_sec: int = 600
    capture_output: bool = True

    def to_payload(self) -> Dict[str, Any]:
        return {
            "cmd": self.cmd,
            "cwd": self.cwd,
            "env": dict(self.env),
            "timeout_sec": self.timeout_sec,
            "capture_output": self.capture_output,
        }


@dataclass(frozen=True)
class ExecResponse:
    trial_id: str
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ArtifactEntry:
    path: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactsResponse:
    trial_id: str
    artifacts: Sequence[ArtifactEntry]
    raw: Mapping[str, Any]

