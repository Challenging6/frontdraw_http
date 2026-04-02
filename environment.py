from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .adapter import (
    build_exec_request,
    build_prepare_tarball_request,
    build_trial_create_request,
    build_verifier_exec_request,
    package_task_dir,
)
from .client import FrontdrawHttpClient
from .models import ArtifactsResponse, ExecResponse, PrepareResponse, TrialCreateResponse


@dataclass(frozen=True)
class RuntimeConfig:
    metadata: Mapping[str, Any]
    environment: Mapping[str, Any]
    agent: Mapping[str, Any]
    verifier: Mapping[str, Any]
    skills: Mapping[str, Any]
    render: Mapping[str, Any]


@dataclass(frozen=True)
class TrialHandle:
    task_dir: Path
    runtime: RuntimeConfig
    create_request: Mapping[str, Any]
    create_response: TrialCreateResponse

    @property
    def trial_id(self) -> str:
        return self.create_response.trial_id

    @property
    def workspace_root(self) -> str:
        return self.create_response.workspace_root

    @property
    def bundle_profile(self) -> str:
        return str(self.runtime.skills.get("bundle_profile", "none"))

    @property
    def home_profile(self) -> str:
        return str(self.runtime.agent.get("home_profile", "codex"))


def load_runtime_config(task_dir: str | Path) -> RuntimeConfig:
    path = Path(task_dir) / "environment" / "runtime.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RuntimeConfig(
        metadata=payload.get("metadata", {}),
        environment=payload.get("environment", {}),
        agent=payload.get("agent", {}),
        verifier=payload.get("verifier", {}),
        skills=payload.get("skills", {}),
        render=payload.get("render", {}),
    )


class FrontdrawHttpEnvironment:
    def __init__(self, client: FrontdrawHttpClient) -> None:
        self.client = client

    def create_trial(
        self,
        task_dir: str | Path,
        run_id: str,
        image: str | None = None,
        timeout_sec: int | None = None,
    ) -> TrialHandle:
        task_dir = Path(task_dir).resolve()
        runtime = load_runtime_config(task_dir)
        request = build_trial_create_request(
            task_dir=task_dir,
            run_id=run_id,
            image=image or str(runtime.environment.get("image", "frontdrawskill-bench:base")),
            timeout_sec=timeout_sec or int(runtime.agent.get("timeout_sec", 1800)),
        )
        response = self.client.create_trial(request)
        return TrialHandle(
            task_dir=task_dir,
            runtime=runtime,
            create_request=request.to_payload(),
            create_response=response,
        )

    def prepare_from_tarball(self, handle: TrialHandle, tarball_url: str) -> PrepareResponse:
        request = build_prepare_tarball_request(tarball_url)
        return self.client.prepare_trial(handle.trial_id, request)

    def exec_agent(
        self,
        handle: TrialHandle,
        cmd: str,
        timeout_sec: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> ExecResponse:
        request = build_exec_request(
            cmd=cmd,
            workspace_root=handle.workspace_root,
            home_profile=handle.home_profile,
            bundle_profile=handle.bundle_profile,
            timeout_sec=timeout_sec or int(handle.runtime.agent.get("timeout_sec", 1800)),
            extra_env=extra_env,
        )
        return self.client.exec_trial(handle.trial_id, request)

    def exec_verifier(
        self,
        handle: TrialHandle,
        timeout_sec: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> ExecResponse:
        request = build_verifier_exec_request(
            workspace_root=handle.workspace_root,
            home_profile=handle.home_profile,
            bundle_profile=handle.bundle_profile,
            timeout_sec=timeout_sec or int(handle.runtime.verifier.get("timeout_sec", 900)),
            extra_env=extra_env,
        )
        return self.client.exec_trial(handle.trial_id, request)

    def list_artifacts(self, handle: TrialHandle) -> ArtifactsResponse:
        return self.client.list_artifacts(handle.trial_id)

    def download_artifacts(self, handle: TrialHandle, output_dir: str | Path, paths: Sequence[str] | None = None) -> Dict[str, Path]:
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_paths = list(paths) if paths is not None else [item.path for item in self.list_artifacts(handle).artifacts]
        downloaded: Dict[str, Path] = {}
        for rel_path in artifact_paths:
            payload = self.client.download_file(handle.trial_id, rel_path)
            target = output_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            downloaded[rel_path] = target
        return downloaded

    def cleanup(self, handle: TrialHandle) -> Mapping[str, Any]:
        return self.client.delete_trial(handle.trial_id)

    def package_task_dir(self, handle: TrialHandle, output_path: str | Path) -> Path:
        return package_task_dir(handle.task_dir, output_path)

