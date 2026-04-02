from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping

from .models import ExecRequest, PrepareTarballRequest, TrialCreateRequest


def load_instance_task(task_dir: str | Path) -> Dict[str, Any]:
    task_path = Path(task_dir) / "assets" / "task.json"
    return json.loads(task_path.read_text(encoding="utf-8"))


def compute_trial_hash(
    task_id: str,
    technology: str,
    condition: str,
    bundle_version: str,
    run_id: str,
) -> str:
    key = "||".join([task_id, technology, condition, bundle_version, run_id])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def resolved_bundle_version(instance_task: Mapping[str, Any]) -> str:
    condition = str(instance_task.get("condition", "bare"))
    if condition == "bundle":
        return str(instance_task.get("bundle_profile", "all_skills_v1_alpha"))
    return "none"


def build_trial_create_request(
    task_dir: str | Path,
    run_id: str,
    image: str = "frontdrawskill-bench:base",
    timeout_sec: int = 1800,
) -> TrialCreateRequest:
    instance_task = load_instance_task(task_dir)
    bundle_version = resolved_bundle_version(instance_task)
    return TrialCreateRequest(
        trial_hash=compute_trial_hash(
            task_id=str(instance_task["task_id"]),
            technology=str(instance_task["technology"]),
            condition=str(instance_task["condition"]),
            bundle_version=bundle_version,
            run_id=run_id,
        ),
        image=image,
        task_id=str(instance_task["task_id"]),
        technology=str(instance_task["technology"]),
        condition=str(instance_task["condition"]),
        bundle_profile=bundle_version,
        timeout_sec=timeout_sec,
    )


def build_prepare_tarball_request(tarball_url: str) -> PrepareTarballRequest:
    return PrepareTarballRequest(tarball_url=tarball_url)


def package_task_dir(task_dir: str | Path, output_path: str | Path) -> Path:
    task_dir = Path(task_dir).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as archive:
        for path in sorted(task_dir.rglob("*")):
            archive.add(path, arcname=path.relative_to(task_dir))
    return output_path


def build_agent_env(
    workspace_root: str,
    home_profile: str = "codex",
    bundle_profile: str = "none",
    extra_env: Mapping[str, str] | None = None,
) -> Dict[str, str]:
    env: Dict[str, str] = {
        "BUNDLE_PROFILE": bundle_profile,
    }
    if home_profile == "codex":
        env["CODEX_HOME"] = f"{workspace_root}/agent-home/codex"
    elif home_profile == "claude":
        env["CLAUDE_CONFIG_DIR"] = f"{workspace_root}/agent-home/claude"
    elif home_profile == "gemini":
        env["GEMINI_CONFIG_DIR"] = f"{workspace_root}/agent-home/gemini"
    else:
        raise ValueError(f"Unsupported home profile: {home_profile}")
    if extra_env:
        env.update(extra_env)
    return env


def build_exec_request(
    cmd: str,
    workspace_root: str,
    home_profile: str = "codex",
    bundle_profile: str = "none",
    timeout_sec: int = 600,
    extra_env: Mapping[str, str] | None = None,
) -> ExecRequest:
    return ExecRequest(
        cmd=cmd,
        cwd=workspace_root,
        env=build_agent_env(
            workspace_root=workspace_root,
            home_profile=home_profile,
            bundle_profile=bundle_profile,
            extra_env=extra_env,
        ),
        timeout_sec=timeout_sec,
        capture_output=True,
    )


def build_verifier_exec_request(
    workspace_root: str,
    home_profile: str = "codex",
    bundle_profile: str = "none",
    timeout_sec: int = 900,
    extra_env: Mapping[str, str] | None = None,
) -> ExecRequest:
    return build_exec_request(
        cmd="bash tests/test.sh",
        workspace_root=workspace_root,
        home_profile=home_profile,
        bundle_profile=bundle_profile,
        timeout_sec=timeout_sec,
        extra_env=extra_env,
    )

