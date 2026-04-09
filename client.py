from __future__ import annotations

import json
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence

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


class FrontdrawHttpError(RuntimeError):
    """Raised when the shared sandbox service returns an error."""


class FrontdrawHttpClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str | None = None,
        timeout_sec: int = 30,
        user_agent: str = "frontdraw-http-client/0.1",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent

    def create_trial(self, request: TrialCreateRequest) -> TrialCreateResponse:
        payload = self._request_json("POST", "/trials", request.to_payload())
        return TrialCreateResponse(
            trial_id=str(payload["trial_id"]),
            sandbox_id=str(payload["sandbox_id"]),
            workspace_root=str(payload["workspace_root"]),
            image=str(payload["image"]),
            status=str(payload["status"]),
            raw=payload,
        )

    def prepare_trial(self, trial_id: str, request: PrepareRefsRequest | PrepareTarballRequest) -> PrepareResponse:
        payload = self._request_json("POST", f"/trials/{trial_id}/prepare", request.to_payload())
        return PrepareResponse(
            trial_id=str(payload["trial_id"]),
            prepared=bool(payload["prepared"]),
            workspace_root=str(payload["workspace_root"]),
            written_paths=list(payload.get("written_paths", [])),
            raw=payload,
        )

    def exec_trial(self, trial_id: str, request: ExecRequest) -> ExecResponse:
        payload = self._request_json("POST", f"/trials/{trial_id}/exec", request.to_payload())
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
        payload = self._request_json("GET", f"/trials/{trial_id}/artifacts")
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
        encoded = urllib.parse.quote(artifact_path, safe="")
        return self._request_bytes("GET", f"/trials/{trial_id}/files/{encoded}")

    def delete_trial(self, trial_id: str) -> Mapping[str, Any]:
        return self._request_json("DELETE", f"/trials/{trial_id}")

    def upload_file(self, trial_id: str, source_path: Path | str, target_path: str) -> Mapping[str, Any]:
        source = Path(source_path)
        encoded = urllib.parse.quote(target_path, safe="")
        return self._request_json_bytes(
            "POST",
            f"/trials/{trial_id}/upload-file?target_path={encoded}",
            source.read_bytes(),
            content_type="application/octet-stream",
        )

    def upload_dir(self, trial_id: str, source_dir: Path | str, target_dir: str) -> Mapping[str, Any]:
        source = Path(source_dir)
        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(source.rglob("*")):
                archive.add(path, arcname=path.relative_to(source))
        encoded = urllib.parse.quote(target_dir, safe="")
        return self._request_json_bytes(
            "POST",
            f"/trials/{trial_id}/upload-dir?target_dir={encoded}",
            buffer.getvalue(),
            content_type="application/gzip",
        )

    def download_env_file(self, trial_id: str, source_path: str) -> bytes:
        encoded = urllib.parse.quote(source_path, safe="")
        return self._request_bytes("GET", f"/trials/{trial_id}/download-file?source_path={encoded}")

    def download_dir(self, trial_id: str, source_dir: str, target_dir: Path | str) -> Path:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        encoded = urllib.parse.quote(source_dir, safe="")
        payload = self._request_bytes("GET", f"/trials/{trial_id}/download-dir?source_dir={encoded}")
        with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(target)
        return target

    def _request_json(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        raw = self._request(method=method, path=path, payload=payload)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise FrontdrawHttpError(f"Non-JSON response from {method} {path}: {raw[:200]!r}") from exc

    def _request_json_bytes(
        self,
        method: str,
        path: str,
        payload: bytes,
        *,
        content_type: str,
    ) -> Mapping[str, Any]:
        raw = self._request_bytes_with_payload(method=method, path=path, payload=payload, content_type=content_type)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise FrontdrawHttpError(f"Non-JSON response from {method} {path}: {raw[:200]!r}") from exc

    def _request_bytes(self, method: str, path: str) -> bytes:
        return self._request(method=method, path=path, payload=None)

    def _request_bytes_with_payload(
        self,
        method: str,
        path: str,
        payload: bytes,
        *,
        content_type: str,
    ) -> bytes:
        return self._request_raw(method=method, path=path, data=payload, content_type=content_type)

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None) -> bytes:
        data = None
        headers = {
            "User-Agent": self.user_agent,
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return self._request_raw(method=method, path=path, data=data, content_type="application/json")
        return self._request_raw(method=method, path=path, data=None, content_type=None)

    def _request_raw(
        self,
        *,
        method: str,
        path: str,
        data: bytes | None,
        content_type: str | None,
    ) -> bytes:
        headers = {
            "User-Agent": self.user_agent,
        }
        if content_type:
            headers["Content-Type"] = content_type
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=self.timeout_sec) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = body.decode("utf-8", errors="replace")
            raise FrontdrawHttpError(f"{method} {path} failed with HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise FrontdrawHttpError(f"{method} {path} failed: {exc}") from exc
