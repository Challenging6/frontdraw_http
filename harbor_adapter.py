from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .client import FrontdrawHttpClient
from .environment import FrontdrawHttpEnvironment, TrialHandle, load_runtime_config


@dataclass(frozen=True)
class HarborRunContext:
    """Minimal run context that a Harbor environment bridge needs."""

    task_dir: Path
    run_id: str
    runtime: Any
    handle: TrialHandle

    @property
    def trial_id(self) -> str:
        return self.handle.trial_id

    @property
    def workspace_root(self) -> str:
        return self.handle.workspace_root


@dataclass(frozen=True)
class HarborAdapterConfig:
    """
    Runtime config that a real Harbor BaseEnvironment plugin would usually receive.
    """

    base_url: str
    api_key: str | None = None
    request_timeout_sec: int = 30
    image_override: str | None = None

    def build_client(self) -> FrontdrawHttpClient:
        return FrontdrawHttpClient(
            base_url=self.base_url,
            bearer_token=self.api_key,
            timeout_sec=self.request_timeout_sec,
        )


@dataclass(frozen=True)
class HarborRunResult:
    create: Mapping[str, Any]
    prepare: Mapping[str, Any]
    agent_exec: Mapping[str, Any] | None
    verifier_exec: Mapping[str, Any] | None
    artifacts: Mapping[str, Any]
    downloaded_artifacts: Mapping[str, str]
    cleanup: Mapping[str, Any] | None


class FrontdrawHarborAdapter:
    """
    Thin bridge from Harbor-style task execution to the existing frontdraw-http lifecycle.

    This class intentionally avoids depending on Harbor internals. It provides the small set
    of operations that a future BaseEnvironment adapter will need:
    - create one isolated trial for a task instance
    - prepare the trial from a packaged task tarball
    - run the agent command
    - run the verifier command
    - download artifacts
    - cleanup the trial
    """

    def __init__(self, environment: FrontdrawHttpEnvironment) -> None:
        self.environment = environment

    @classmethod
    def from_config(cls, config: HarborAdapterConfig) -> "FrontdrawHarborAdapter":
        return cls(FrontdrawHttpEnvironment(config.build_client()))

    def setup_run(
        self,
        task_dir: str | Path,
        run_id: str,
        image: str | None = None,
        timeout_sec: int | None = None,
    ) -> HarborRunContext:
        task_dir_path = Path(task_dir).resolve()
        runtime = load_runtime_config(task_dir_path)
        handle = self.environment.create_trial(
            task_dir=task_dir_path,
            run_id=run_id,
            image=image,
            timeout_sec=timeout_sec,
        )
        return HarborRunContext(
            task_dir=task_dir_path,
            run_id=run_id,
            runtime=runtime,
            handle=handle,
        )

    def package_task(self, context: HarborRunContext, output_path: str | Path) -> Path:
        return self.environment.package_task_dir(context.handle, output_path)

    def prepare_run(self, context: HarborRunContext, tarball_url: str) -> Mapping[str, Any]:
        return self.environment.prepare_from_tarball(context.handle, tarball_url).raw

    def run_agent(
        self,
        context: HarborRunContext,
        agent_cmd: str,
        timeout_sec: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        response = self.environment.exec_agent(
            context.handle,
            cmd=agent_cmd,
            timeout_sec=timeout_sec,
            extra_env=extra_env,
        )
        return dict(response.raw)

    def run_verifier(
        self,
        context: HarborRunContext,
        timeout_sec: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        response = self.environment.exec_verifier(
            context.handle,
            timeout_sec=timeout_sec,
            extra_env=extra_env,
        )
        return dict(response.raw)

    def list_artifacts(self, context: HarborRunContext) -> Mapping[str, Any]:
        return dict(self.environment.list_artifacts(context.handle).raw)

    def download_artifacts(
        self,
        context: HarborRunContext,
        output_dir: str | Path,
        paths: Sequence[str] | None = None,
    ) -> Dict[str, Path]:
        return self.environment.download_artifacts(
            context.handle,
            output_dir=output_dir,
            paths=paths,
        )

    def fetch_reward_json(self, context: HarborRunContext, output_dir: str | Path) -> Path | None:
        artifacts = self.environment.list_artifacts(context.handle)
        reward_paths = [entry.path for entry in artifacts.artifacts if entry.path == "reward.json"]
        if not reward_paths:
            return None
        downloaded = self.environment.download_artifacts(
            context.handle,
            output_dir=output_dir,
            paths=reward_paths,
        )
        return downloaded.get("reward.json")

    def cleanup_run(self, context: HarborRunContext) -> Mapping[str, Any]:
        return self.environment.cleanup(context.handle)

    def run_once(
        self,
        task_dir: str | Path,
        run_id: str,
        agent_cmd: str,
        *,
        work_dir: str | Path,
        tarball_url: str | None = None,
        image: str | None = None,
        agent_timeout_sec: int | None = None,
        verifier_timeout_sec: int | None = None,
        extra_agent_env: Mapping[str, str] | None = None,
        extra_verifier_env: Mapping[str, str] | None = None,
        run_verifier: bool = True,
        keep_trial: bool = False,
    ) -> HarborRunResult:
        """
        Run one complete Harbor-style task instance through frontdraw-http.

        If `tarball_url` is omitted, this method will package the task locally and use a
        `file://` URL. That is useful for local / shared-filesystem smoke runs, but a real
        Harbor deployment will usually provide an HTTP/object-storage URL instead.
        """

        context = self.setup_run(task_dir=task_dir, run_id=run_id, image=image)
        tmp_tarball: tempfile.TemporaryDirectory[str] | None = None
        result_payload: HarborRunResult | None = None
        try:
            effective_tarball_url = tarball_url
            if effective_tarball_url is None:
                tmp_tarball = tempfile.TemporaryDirectory(prefix="frontdraw-http-tarball-")
                tarball_path = Path(tmp_tarball.name) / f"{context.task_dir.name}.tar.gz"
                packaged = self.package_task(context, tarball_path)
                effective_tarball_url = packaged.resolve().as_uri()

            prepare = self.prepare_run(context, effective_tarball_url)
            agent_exec = self.run_agent(
                context,
                agent_cmd=agent_cmd,
                timeout_sec=agent_timeout_sec,
                extra_env=extra_agent_env,
            )
            verifier_exec = None
            if run_verifier:
                verifier_exec = self.run_verifier(
                    context,
                    timeout_sec=verifier_timeout_sec,
                    extra_env=extra_verifier_env,
                )
            artifacts = self.list_artifacts(context)
            downloaded = self.download_artifacts(context, work_dir)
            downloaded_str = {key: str(value) for key, value in downloaded.items()}
            result_payload = HarborRunResult(
                create=dict(context.handle.create_response.raw),
                prepare=dict(prepare),
                agent_exec=dict(agent_exec),
                verifier_exec=dict(verifier_exec) if verifier_exec is not None else None,
                artifacts=dict(artifacts),
                downloaded_artifacts=downloaded_str,
                cleanup=None,
            )
        finally:
            cleanup_payload: Mapping[str, Any] | None = None
            if not keep_trial:
                cleanup_payload = self.cleanup_run(context)
            if tmp_tarball is not None:
                tmp_tarball.cleanup()
        assert result_payload is not None
        return HarborRunResult(
            create=result_payload.create,
            prepare=result_payload.prepare,
            agent_exec=result_payload.agent_exec,
            verifier_exec=result_payload.verifier_exec,
            artifacts=result_payload.artifacts,
            downloaded_artifacts=result_payload.downloaded_artifacts,
            cleanup=cleanup_payload,
        )
