from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


WORKSPACE_ROOT = Path(os.environ.get("FRONTDRAW_WORKSPACE_ROOT", "/workspaces")).resolve()
SANDBOX_ID = os.environ.get("FRONTDRAW_SANDBOX_ID", "shared-sandbox-001")


app = FastAPI(title="frontdraw-http sandbox service", version="0.1.0")
app.state.trials = {}


def _ensure_workspace_root() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


def _workspace_path(trial_hash: str) -> Path:
    return (WORKSPACE_ROOT / trial_hash).resolve()


def _trial_meta_path(workspace_root: Path) -> Path:
    return workspace_root / ".trial_meta.json"


def _write_trial_meta(meta: Mapping[str, Any]) -> None:
    workspace_root = Path(meta["workspace_root"])
    _trial_meta_path(workspace_root).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown trial_id: {trial_id}")
    return meta


def _ensure_support_dirs(workspace_root: Path, agent_home_profiles: Iterable[str] | None = None) -> List[str]:
    written: List[str] = []
    for rel in ["logs", "submission", "skills", "assets", "task", "agent-home"]:
        path = workspace_root / rel
        path.mkdir(parents=True, exist_ok=True)
        written.append(rel)
    for profile in agent_home_profiles or []:
        profile_path = workspace_root / "agent-home" / profile / "skills"
        profile_path.mkdir(parents=True, exist_ok=True)
        written.append(str(profile_path.relative_to(workspace_root)))
    return written


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


@app.on_event("startup")
def _startup() -> None:
    _ensure_workspace_root()


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
    cwd = payload.get("cwd", str(workspace_root))
    cwd_path = _safe_resolve_under(workspace_root, str(cwd))
    env = os.environ.copy()
    for key, value in payload.get("env", {}).items():
        env[str(key)] = str(value)
    timeout_sec = int(payload.get("timeout_sec", 600))

    started = time.time()
    process = subprocess.Popen(
        str(cmd),
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
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()

    duration_ms = int((time.time() - started) * 1000)
    stdout_log, stderr_log = _persist_exec_logs(workspace_root, stdout, stderr)
    meta["last_exec"] = {
        "cmd": str(cmd),
        "cwd": str(cwd_path),
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "exit_code": process.returncode,
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
