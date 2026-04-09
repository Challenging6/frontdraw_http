from __future__ import annotations

import tarfile
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from fastapi.testclient import TestClient

from .models import (
    ArtifactEntry,
    ArtifactsResponse,
    ExecRequest,
    ExecResponse,
    PrepareRefsRequest,
    PrepareResponse,
    PrepareTarballRequest,
    TrialCreateRequest,
    TrialCreateResponse,
)


class InprocessFrontdrawHttpClient:
    """
    Drop-in client for local smoke runs when loopback HTTP is blocked by the environment.
    """

    def __init__(self, app: Any) -> None:
        self.client = TestClient(app)

    def create_trial(self, request: TrialCreateRequest) -> TrialCreateResponse:
        response = self.client.post("/trials", json=request.to_payload())
        response.raise_for_status()
        payload = response.json()
        return TrialCreateResponse(
            trial_id=str(payload["trial_id"]),
            sandbox_id=str(payload["sandbox_id"]),
            workspace_root=str(payload["workspace_root"]),
            image=str(payload["image"]),
            status=str(payload["status"]),
            raw=payload,
        )

    def prepare_trial(self, trial_id: str, request: PrepareRefsRequest | PrepareTarballRequest) -> PrepareResponse:
        response = self.client.post(f"/trials/{trial_id}/prepare", json=request.to_payload())
        response.raise_for_status()
        payload = response.json()
        return PrepareResponse(
            trial_id=str(payload["trial_id"]),
            prepared=bool(payload["prepared"]),
            workspace_root=str(payload["workspace_root"]),
            written_paths=list(payload.get("written_paths", [])),
            raw=payload,
        )

    def exec_trial(self, trial_id: str, request: ExecRequest) -> ExecResponse:
        response = self.client.post(f"/trials/{trial_id}/exec", json=request.to_payload())
        response.raise_for_status()
        payload = response.json()
        return ExecResponse(
            trial_id=str(payload["trial_id"]),
            exit_code=int(payload["exit_code"]),
            duration_ms=int(payload["duration_ms"]),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            timed_out=bool(payload.get("timed_out", False)),
            raw=payload,
        )

    def list_artifacts(self, trial_id: str) -> ArtifactsResponse:
        response = self.client.get(f"/trials/{trial_id}/artifacts")
        response.raise_for_status()
        payload = response.json()
        artifacts = [
            ArtifactEntry(path=str(item["path"]), size_bytes=int(item["size_bytes"]))
            for item in payload.get("artifacts", [])
        ]
        return ArtifactsResponse(
            trial_id=str(payload["trial_id"]),
            artifacts=artifacts,
            raw=payload,
        )

    def download_file(self, trial_id: str, artifact_path: str) -> bytes:
        response = self.client.get(f"/trials/{trial_id}/files/{artifact_path}")
        response.raise_for_status()
        return response.content

    def upload_file(self, trial_id: str, source_path: Path | str, target_path: str) -> Mapping[str, Any]:
        response = self.client.post(
            f"/trials/{trial_id}/upload-file",
            params={"target_path": str(target_path)},
            content=Path(source_path).read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()
        return response.json()

    def upload_dir(self, trial_id: str, source_dir: Path | str, target_dir: str) -> Mapping[str, Any]:
        buffer = BytesIO()
        source = Path(source_dir)
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(source.rglob("*")):
                archive.add(path, arcname=path.relative_to(source))
        response = self.client.post(
            f"/trials/{trial_id}/upload-dir",
            params={"target_dir": str(target_dir)},
            content=buffer.getvalue(),
            headers={"Content-Type": "application/gzip"},
        )
        response.raise_for_status()
        return response.json()

    def download_env_file(self, trial_id: str, source_path: str) -> bytes:
        response = self.client.get(f"/trials/{trial_id}/download-file", params={"source_path": source_path})
        response.raise_for_status()
        return response.content

    def download_dir(self, trial_id: str, source_dir: str, target_dir: Path | str) -> Path:
        response = self.client.get(f"/trials/{trial_id}/download-dir", params={"source_dir": source_dir})
        response.raise_for_status()
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=BytesIO(response.content), mode="r:gz") as archive:
            archive.extractall(target)
        return target

    def delete_trial(self, trial_id: str) -> Mapping[str, Any]:
        response = self.client.delete(f"/trials/{trial_id}")
        response.raise_for_status()
        return response.json()
