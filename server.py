from __future__ import annotations

import io
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response


WORKSPACE_ROOT = Path(os.environ.get("FRONTDRAW_WORKSPACE_ROOT", "/workspaces")).resolve()
SANDBOX_ID = os.environ.get("FRONTDRAW_SANDBOX_ID", "shared-sandbox-001")


app = FastAPI(title="frontdraw-http sandbox service", version="0.1.0")
app.state.trials = {}


def _ensure_workspace_root() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    _trial_registry_dir().mkdir(parents=True, exist_ok=True)


def _workspace_path(trial_hash: str) -> Path:
    return (WORKSPACE_ROOT / trial_hash).resolve()


def _trial_meta_path(workspace_root: Path) -> Path:
    return workspace_root / ".trial_meta.json"


def _trial_registry_dir() -> Path:
    return WORKSPACE_ROOT / ".trials"


def _trial_registry_path(trial_id: str) -> Path:
    return _trial_registry_dir() / f"{trial_id}.json"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _write_trial_meta(meta: Mapping[str, Any]) -> None:
    workspace_root = Path(meta["workspace_root"])
    _atomic_write_json(_trial_meta_path(workspace_root), meta)
    _atomic_write_json(_trial_registry_path(str(meta["trial_id"])), meta)


def _delete_trial_meta(trial_id: str, workspace_root: Path) -> None:
    for path in [_trial_registry_path(trial_id), _trial_meta_path(workspace_root)]:
        if path.exists():
            path.unlink()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_resolve_under(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (root / path).resolve()
    if not _is_relative_to(resolved, root):
        raise HTTPException(status_code=400, detail=f"Path escapes workspace: {value}")
    return resolved


def _safe_extract_tar_bytes(blob: bytes, target_dir: Path) -> List[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
        for member in archive.getmembers():
            member_path = (target_dir / member.name).resolve()
            if not _is_relative_to(member_path, target_dir):
                raise HTTPException(status_code=400, detail=f"Unsafe tar entry: {member.name}")
        archive.extractall(target_dir)
        for member in archive.getmembers():
            written.append(str((target_dir / member.name).relative_to(target_dir)))
    return written


def _pack_dir_to_tgz_bytes(source_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(source_dir.rglob("*")):
            archive.add(path, arcname=path.relative_to(source_dir))
    return buffer.getvalue()


def _download_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def _copy_or_extract_ref(ref: str, target_path: Path) -> List[str]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(ref)
    if parsed.scheme in {"http", "https"}:
        blob = _download_bytes(ref)
        if ref.endswith((".tar.gz", ".tgz", ".tar")):
            return _safe_extract_tar_bytes(blob, target_path)
        target_path.write_bytes(blob)
        return [str(target_path.name)]
    if parsed.scheme == "file":
        source = Path(urllib.request.url2pathname(parsed.path))
    else:
        source = Path(ref)
    source = source.resolve()
    if not source.exists():
        raise HTTPException(status_code=400, detail=f"Ref does not exist: {ref}")
    if source.is_dir():
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.copytree(source, target_path)
        return [str(path.relative_to(target_path.parent)) for path in sorted(target_path.rglob("*"))]
    if source.name.endswith((".tar.gz", ".tgz", ".tar")):
        return _safe_extract_tar_bytes(source.read_bytes(), target_path)
    shutil.copy2(source, target_path)
    return [str(target_path.name)]


def _load_trial(trial_id: str) -> Dict[str, Any]:
    meta = app.state.trials.get(trial_id)
    if meta is not None:
        return meta
    registry_path = _trial_registry_path(trial_id)
    if registry_path.exists():
        meta = json.loads(registry_path.read_text(encoding="utf-8"))
        app.state.trials[trial_id] = meta
        return meta
    raise HTTPException(status_code=404, detail=f"Unknown trial_id: {trial_id}")


def _ensure_support_dirs(workspace_root: Path, agent_home_profiles: Iterable[str] | None = None) -> List[str]:
    written: List[str] = []
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace_root.chmod(0o777)
    for rel in [
        "logs",
        "logs/agent",
        "logs/verifier",
        "logs/artifacts",
        "submission",
        "skills",
        "assets",
        "task",
        "tests",
        "solution",
        "agent-home",
    ]:
        path = workspace_root / rel
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o777)
        written.append(rel)
    for profile in agent_home_profiles or []:
        profile_path = workspace_root / "agent-home" / profile / "skills"
        profile_path.mkdir(parents=True, exist_ok=True)
        profile_path.chmod(0o777)
        written.append(str(profile_path.relative_to(workspace_root)))
    return written


def _wrap_command_for_user(command: str, user: str | None) -> str:
    if not user or user in {"root", "0"}:
        return command
    return f"su -s /bin/sh -c {shlex.quote(command)} {shlex.quote(user)}"


def _persist_exec_logs(workspace_root: Path, stdout: str, stderr: str) -> Tuple[str, str]:
    logs_dir = workspace_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    stdout_path = logs_dir / f"exec_{stamp}.stdout.txt"
    stderr_path = logs_dir / f"exec_{stamp}.stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return (
        str(stdout_path.relative_to(workspace_root)),
        str(stderr_path.relative_to(workspace_root)),
    )


def _load_runtime_profiles(workspace_root: Path) -> List[str]:
    runtime_path = workspace_root / "environment" / "runtime.json"
    if not runtime_path.exists():
        return []
    try:
        payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    agent = payload.get("agent", {})
    home_profile = agent.get("home_profile")
    if not home_profile:
        return []
    return [str(home_profile)]


def _prepare_exec_workspace(workspace_root: Path, env: Mapping[str, str]) -> None:
    profiles = list(_load_runtime_profiles(workspace_root))
    for env_key in ["CODEX_HOME", "CLAUDE_CONFIG_DIR", "GEMINI_CONFIG_DIR"]:
        value = env.get(env_key)
        if value:
            path = _safe_resolve_under(workspace_root, value)
            (path / "skills").mkdir(parents=True, exist_ok=True)
            if path.name not in profiles:
                profiles.append(path.name)
    _ensure_support_dirs(workspace_root, profiles)


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _terminate_process_group(process: subprocess.Popen[str], grace_period_sec: float = 2.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace_period_sec
    while time.time() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


@app.on_event("startup")
def _startup() -> None:
    _ensure_workspace_root()


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {
        "ok": True,
        "sandbox_id": SANDBOX_ID,
        "workspace_root": str(WORKSPACE_ROOT),
    }


@app.post("/trials")
def create_trial(payload: Dict[str, Any]) -> Dict[str, Any]:
    required = ["trial_hash", "image", "task_id", "technology", "condition", "bundle_profile", "timeout_sec"]
    for key in required:
        if key not in payload:
            raise HTTPException(status_code=400, detail=f"Missing field: {key}")
    trial_hash = str(payload["trial_hash"])
    workspace_root = _workspace_path(trial_hash)
    if workspace_root.exists():
        raise HTTPException(status_code=409, detail=f"Workspace already exists for trial_hash={trial_hash}")
    workspace_root.mkdir(parents=True, exist_ok=False)
    workspace_root.chmod(0o777)
    trial_id = f"trial_{secrets.token_hex(6)}"
    meta = {
        "trial_id": trial_id,
        "trial_hash": trial_hash,
        "sandbox_id": SANDBOX_ID,
        "workspace_root": str(workspace_root),
        "image": str(payload["image"]),
        "task_id": str(payload["task_id"]),
        "technology": str(payload["technology"]),
        "condition": str(payload["condition"]),
        "bundle_profile": str(payload["bundle_profile"]),
        "timeout_sec": int(payload["timeout_sec"]),
        "status": "ready",
        "created_at": int(time.time()),
    }
    app.state.trials[trial_id] = meta
    _write_trial_meta(meta)
    return {
        "trial_id": trial_id,
        "sandbox_id": SANDBOX_ID,
        "workspace_root": str(workspace_root),
        "image": meta["image"],
        "status": "ready",
    }


@app.post("/trials/{trial_id}/prepare")
def prepare_trial(trial_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    written_paths: List[str] = []

    if payload.get("upload_mode") == "tarball":
        tarball_url = payload.get("tarball_url")
        if not tarball_url:
            raise HTTPException(status_code=400, detail="tarball_url is required when upload_mode=tarball")
        blob = _download_bytes(str(tarball_url))
        written_paths.extend(_safe_extract_tar_bytes(blob, workspace_root))
        written_paths.extend(_ensure_support_dirs(workspace_root))
    else:
        instruction_text = payload.get("instruction_text")
        task_toml_text = payload.get("task_toml_text")
        if instruction_text is not None:
            (workspace_root / "instruction.md").write_text(str(instruction_text), encoding="utf-8")
            written_paths.append("instruction.md")
        if task_toml_text is not None:
            (workspace_root / "task.toml").write_text(str(task_toml_text), encoding="utf-8")
            written_paths.append("task.toml")
        assets_ref = payload.get("task_assets_ref")
        if assets_ref:
            written_paths.extend(_copy_or_extract_ref(str(assets_ref), workspace_root / "assets"))
        for item in payload.get("data_refs", []):
            target_path = _safe_resolve_under(workspace_root, str(item["target_path"]))
            written_paths.extend(_copy_or_extract_ref(str(item["content_ref"]), target_path))
        skills_ref = payload.get("skills_ref")
        if skills_ref:
            written_paths.extend(_copy_or_extract_ref(str(skills_ref), workspace_root / "skills"))
        written_paths.extend(_ensure_support_dirs(workspace_root, payload.get("agent_home_profiles", [])))

    meta["status"] = "prepared"
    meta["last_prepare"] = {
        "mode": str(payload.get("upload_mode", "refs")),
        "written_paths_count": len(sorted(set(written_paths))),
        "prepared_at": int(time.time()),
    }
    _write_trial_meta(meta)
    return {
        "trial_id": trial_id,
        "prepared": True,
        "workspace_root": str(workspace_root),
        "written_paths": sorted(set(written_paths)),
    }


@app.post("/trials/{trial_id}/exec")
def exec_trial(trial_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    cmd = payload.get("cmd")
    if not cmd:
        raise HTTPException(status_code=400, detail="cmd is required")
    requested_user = payload.get("user")
    cwd = payload.get("cwd", str(workspace_root))
    cwd_path = _safe_resolve_under(workspace_root, str(cwd))
    cwd_path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key, value in payload.get("env", {}).items():
        env[str(key)] = str(value)
    _prepare_exec_workspace(workspace_root, env)
    timeout_sec = int(payload.get("timeout_sec", 600))
    meta["status"] = "running"
    meta["running_exec"] = {
        "cmd": str(cmd),
        "cwd": str(cwd_path),
        "started_at": int(time.time()),
    }
    _write_trial_meta(meta)

    started = time.time()
    process = subprocess.Popen(
        _wrap_command_for_user(str(cmd), str(requested_user) if requested_user is not None else None),
        shell=True,
        cwd=str(cwd_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        stdout, stderr = process.communicate()

    duration_ms = int((time.time() - started) * 1000)
    stdout_log, stderr_log = _persist_exec_logs(workspace_root, stdout, stderr)
    meta.pop("running_exec", None)
    meta["status"] = "prepared"
    meta["last_exec"] = {
        "cmd": str(cmd),
        "cwd": str(cwd_path),
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "exit_code": process.returncode,
        "finished_at": int(time.time()),
    }
    _write_trial_meta(meta)
    return {
        "trial_id": trial_id,
        "exit_code": process.returncode,
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
    }


@app.post("/trials/{trial_id}/upload-file")
async def upload_file(trial_id: str, request: Request, target_path: str = Query(...)) -> Dict[str, Any]:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    resolved_target = _safe_resolve_under(workspace_root, target_path)
    _ensure_parent_dir(resolved_target)
    payload = await request.body()
    resolved_target.write_bytes(payload)
    return {
        "trial_id": trial_id,
        "uploaded": True,
        "target_path": str(resolved_target.relative_to(workspace_root)),
        "size_bytes": len(payload),
    }


@app.post("/trials/{trial_id}/upload-dir")
async def upload_dir(trial_id: str, request: Request, target_dir: str = Query(...)) -> Dict[str, Any]:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    resolved_target = _safe_resolve_under(workspace_root, target_dir)
    if resolved_target.exists():
        shutil.rmtree(resolved_target)
    payload = await request.body()
    written_paths = _safe_extract_tar_bytes(payload, resolved_target)
    return {
        "trial_id": trial_id,
        "uploaded": True,
        "target_dir": str(resolved_target.relative_to(workspace_root)),
        "written_paths": written_paths,
        "written_paths_count": len(written_paths),
    }


@app.get("/trials/{trial_id}/download-file")
def download_file(trial_id: str, source_path: str = Query(...)) -> FileResponse:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    path = _safe_resolve_under(workspace_root, source_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {source_path}")
    return FileResponse(path)


@app.get("/trials/{trial_id}/download-dir")
def download_dir(trial_id: str, source_dir: str = Query(...)) -> Response:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    path = _safe_resolve_under(workspace_root, source_dir)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {source_dir}")
    payload = _pack_dir_to_tgz_bytes(path)
    filename = f"{path.name or 'root'}.tar.gz"
    return Response(
        content=payload,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/trials/{trial_id}/artifacts")
def list_artifacts(trial_id: str) -> Dict[str, Any]:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    artifacts: List[Dict[str, Any]] = []
    candidates: List[Path] = []
    for rel in ["submission", "logs"]:
        base = workspace_root / rel
        if base.exists():
            candidates.extend(path for path in base.rglob("*") if path.is_file())
    reward_path = workspace_root / "reward.json"
    if reward_path.exists():
        candidates.append(reward_path)
    for path in sorted(candidates):
        artifacts.append(
            {
                "path": str(path.relative_to(workspace_root)),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "trial_id": trial_id,
        "artifacts": artifacts,
    }


@app.get("/trials/{trial_id}/files/{artifact_path:path}")
def get_artifact_file(trial_id: str, artifact_path: str) -> FileResponse:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    path = _safe_resolve_under(workspace_root, artifact_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_path}")
    return FileResponse(path)


@app.delete("/trials/{trial_id}")
def delete_trial(trial_id: str) -> Dict[str, Any]:
    meta = _load_trial(trial_id)
    workspace_root = Path(meta["workspace_root"])
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    app.state.trials.pop(trial_id, None)
    _delete_trial_meta(trial_id, workspace_root)
    return {
        "trial_id": trial_id,
        "deleted": True,
    }


def main() -> None:
    import uvicorn

    host = os.environ.get("FRONTDRAW_HOST", "0.0.0.0")
    port = int(os.environ.get("FRONTDRAW_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
