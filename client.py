from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
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

    def _request_json(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        raw = self._request(method=method, path=path, payload=payload)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise FrontdrawHttpError(f"Non-JSON response from {method} {path}: {raw[:200]!r}") from exc

    def _request_bytes(self, method: str, path: str) -> bytes:
        return self._request(method=method, path=path, payload=None)

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None) -> bytes:
        data = None
        headers = {
            "User-Agent": self.user_agent,
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = body.decode("utf-8", errors="replace")
            raise FrontdrawHttpError(f"{method} {path} failed with HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise FrontdrawHttpError(f"{method} {path} failed: {exc}") from exc

