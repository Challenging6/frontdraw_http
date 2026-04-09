#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapter import (
    build_prepare_tarball_request,
    build_verifier_exec_request,
    build_trial_create_request,
    package_task_dir,
)
from .client import FrontdrawHttpClient
from .environment import FrontdrawHttpEnvironment, load_runtime_config
from .harbor_adapter import FrontdrawHarborAdapter, HarborAdapterConfig
from .inprocess_client import InprocessFrontdrawHttpClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="frontdraw-http shared sandbox helper CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_parser = subparsers.add_parser("package-task", help="Package a built Harbor task directory as tar.gz")
    package_parser.add_argument("--task-dir", required=True)
    package_parser.add_argument("--output", required=True)

    create_parser = subparsers.add_parser("print-create-request", help="Print the create-trial JSON payload for one task instance")
    create_parser.add_argument("--task-dir", required=True)
    create_parser.add_argument("--run-id", required=True)
    create_parser.add_argument("--image", default="frontdrawskill-bench:base")
    create_parser.add_argument("--timeout-sec", type=int, default=1800)

    prepare_parser = subparsers.add_parser("print-prepare-request", help="Print the tarball-based prepare payload")
    prepare_parser.add_argument("--tarball-url", required=True)

    runtime_parser = subparsers.add_parser("print-runtime", help="Print environment/runtime.json for one task instance")
    runtime_parser.add_argument("--task-dir", required=True)

    verifier_parser = subparsers.add_parser("print-verifier-request", help="Print the default verifier exec payload for one task instance")
    verifier_parser.add_argument("--task-dir", required=True)
    verifier_parser.add_argument("--workspace-root", required=True)

    smoke_parser = subparsers.add_parser("smoke-create", help="Call POST /trials once and print the response")
    smoke_parser.add_argument("--base-url", required=True)
    smoke_parser.add_argument("--task-dir", required=True)
    smoke_parser.add_argument("--run-id", required=True)
    smoke_parser.add_argument("--image", default="frontdrawskill-bench:base")
    smoke_parser.add_argument("--timeout-sec", type=int, default=1800)
    smoke_parser.add_argument("--api-key")
    smoke_parser.add_argument("--request-timeout-sec", type=int, default=30)

    lifecycle_parser = subparsers.add_parser("smoke-lifecycle", help="Run create -> prepare -> optional exec -> artifacts -> cleanup against a real sandbox service")
    lifecycle_parser.add_argument("--base-url", required=True)
    lifecycle_parser.add_argument("--task-dir", required=True)
    lifecycle_parser.add_argument("--run-id", required=True)
    lifecycle_parser.add_argument("--tarball-url", required=True)
    lifecycle_parser.add_argument("--image", default="frontdrawskill-bench:base")
    lifecycle_parser.add_argument("--timeout-sec", type=int, default=1800)
    lifecycle_parser.add_argument("--api-key")
    lifecycle_parser.add_argument("--request-timeout-sec", type=int, default=30)
    lifecycle_parser.add_argument("--agent-cmd", help="Optional command to run as the agent step after prepare")
    lifecycle_parser.add_argument("--run-verifier", action="store_true", help="Run verifier after optional agent exec")
    lifecycle_parser.add_argument("--keep-trial", action="store_true", help="Do not delete the trial after execution")
    lifecycle_parser.add_argument("--download-artifacts-dir", help="Optional local directory to download all listed artifacts into")

    adapter_run_parser = subparsers.add_parser("run-adapter", help="Run one task instance through the Harbor-style adapter")
    adapter_run_parser.add_argument("--base-url", required=True)
    adapter_run_parser.add_argument("--task-dir", required=True)
    adapter_run_parser.add_argument("--run-id", required=True)
    adapter_run_parser.add_argument("--agent-cmd", required=True)
    adapter_run_parser.add_argument("--work-dir", required=True, help="Local directory used to download artifacts")
    adapter_run_parser.add_argument("--tarball-url", help="Optional remote tarball URL. If omitted, package locally and use file://")
    adapter_run_parser.add_argument("--image")
    adapter_run_parser.add_argument("--api-key")
    adapter_run_parser.add_argument("--request-timeout-sec", type=int, default=30)
    adapter_run_parser.add_argument("--agent-timeout-sec", type=int)
    adapter_run_parser.add_argument("--verifier-timeout-sec", type=int)
    adapter_run_parser.add_argument("--skip-verifier", action="store_true")
    adapter_run_parser.add_argument("--keep-trial", action="store_true")

    inprocess_parser = subparsers.add_parser("run-adapter-inprocess", help="Run one task instance through the adapter against the in-process FastAPI app")
    inprocess_parser.add_argument("--task-dir", required=True)
    inprocess_parser.add_argument("--run-id", required=True)
    inprocess_parser.add_argument("--agent-cmd", required=True)
    inprocess_parser.add_argument("--work-dir", required=True)
    inprocess_parser.add_argument("--workspace-root", required=True)
    inprocess_parser.add_argument("--image")
    inprocess_parser.add_argument("--agent-timeout-sec", type=int)
    inprocess_parser.add_argument("--verifier-timeout-sec", type=int)
    inprocess_parser.add_argument("--skip-verifier", action="store_true")
    inprocess_parser.add_argument("--keep-trial", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "package-task":
        path = package_task_dir(args.task_dir, args.output)
        print(path)
        return

    if args.command == "print-create-request":
        request = build_trial_create_request(
            task_dir=args.task_dir,
            run_id=args.run_id,
            image=args.image,
            timeout_sec=args.timeout_sec,
        )
        print(json.dumps(request.to_payload(), ensure_ascii=False, indent=2))
        return

    if args.command == "print-prepare-request":
        request = build_prepare_tarball_request(args.tarball_url)
        print(json.dumps(request.to_payload(), ensure_ascii=False, indent=2))
        return

    if args.command == "print-runtime":
        runtime = load_runtime_config(args.task_dir)
        print(
            json.dumps(
                {
                    "metadata": dict(runtime.metadata),
                    "task": dict(runtime.task),
                    "environment": dict(runtime.environment),
                    "agent": dict(runtime.agent),
                    "verifier": dict(runtime.verifier),
                    "skills": dict(runtime.skills),
                    "frontdraw_http": dict(runtime.frontdraw_http),
                    "render": dict(runtime.render),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "print-verifier-request":
        runtime = load_runtime_config(args.task_dir)
        request = build_verifier_exec_request(
            workspace_root=args.workspace_root,
            home_profile=str(runtime.agent.get("home_profile", "codex")),
            bundle_profile=str(runtime.skills.get("bundle_profile", "none")),
            timeout_sec=int(runtime.verifier.get("timeout_sec", 900)),
        )
        print(json.dumps(request.to_payload(), ensure_ascii=False, indent=2))
        return

    if args.command == "smoke-create":
        request = build_trial_create_request(
            task_dir=args.task_dir,
            run_id=args.run_id,
            image=args.image,
            timeout_sec=args.timeout_sec,
        )
        client = FrontdrawHttpClient(
            base_url=args.base_url,
            bearer_token=args.api_key,
            timeout_sec=args.request_timeout_sec,
        )
        response = client.create_trial(request)
        print(json.dumps(dict(response.raw), ensure_ascii=False, indent=2))
        return

    if args.command == "smoke-lifecycle":
        client = FrontdrawHttpClient(
            base_url=args.base_url,
            bearer_token=args.api_key,
            timeout_sec=args.request_timeout_sec,
        )
        environment = FrontdrawHttpEnvironment(client)
        handle = environment.create_trial(
            task_dir=args.task_dir,
            run_id=args.run_id,
            image=args.image,
            timeout_sec=args.timeout_sec,
        )
        lifecycle: dict[str, object] = {
            "create": dict(handle.create_response.raw),
        }
        try:
            prepare = environment.prepare_from_tarball(handle, args.tarball_url)
            lifecycle["prepare"] = dict(prepare.raw)
            if args.agent_cmd:
                agent_exec = environment.exec_agent(handle, cmd=args.agent_cmd)
                lifecycle["agent_exec"] = dict(agent_exec.raw)
            if args.run_verifier:
                verifier_exec = environment.exec_verifier(handle)
                lifecycle["verifier_exec"] = dict(verifier_exec.raw)
            artifacts = environment.list_artifacts(handle)
            lifecycle["artifacts"] = dict(artifacts.raw)
            if args.download_artifacts_dir:
                downloaded = environment.download_artifacts(handle, args.download_artifacts_dir)
                lifecycle["downloaded_artifacts"] = {key: str(value) for key, value in downloaded.items()}
        finally:
            if args.keep_trial:
                lifecycle["cleanup"] = {"skipped": True, "trial_id": handle.trial_id}
            else:
                cleanup = environment.cleanup(handle)
                lifecycle["cleanup"] = dict(cleanup)
        print(json.dumps(lifecycle, ensure_ascii=False, indent=2))
        return

    if args.command == "run-adapter":
        config = HarborAdapterConfig(
            base_url=args.base_url,
            api_key=args.api_key,
            request_timeout_sec=args.request_timeout_sec,
            image_override=args.image,
        )
        adapter = FrontdrawHarborAdapter.from_config(config)
        result = adapter.run_once(
            task_dir=args.task_dir,
            run_id=args.run_id,
            agent_cmd=args.agent_cmd,
            work_dir=args.work_dir,
            tarball_url=args.tarball_url,
            image=args.image,
            agent_timeout_sec=args.agent_timeout_sec,
            verifier_timeout_sec=args.verifier_timeout_sec,
            run_verifier=not args.skip_verifier,
            keep_trial=args.keep_trial,
        )
        print(
            json.dumps(
                {
                    "create": dict(result.create),
                    "prepare": dict(result.prepare),
                    "agent_exec": dict(result.agent_exec) if result.agent_exec is not None else None,
                    "verifier_exec": dict(result.verifier_exec) if result.verifier_exec is not None else None,
                    "artifacts": dict(result.artifacts),
                    "downloaded_artifacts": dict(result.downloaded_artifacts),
                    "cleanup": dict(result.cleanup) if result.cleanup is not None else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "run-adapter-inprocess":
        import harbor.frontdraw_http.server as server_mod

        server_mod.WORKSPACE_ROOT = Path(args.workspace_root).resolve()
        server_mod._ensure_workspace_root()
        server_mod.app.state.trials = {}
        client = InprocessFrontdrawHttpClient(server_mod.app)
        adapter = FrontdrawHarborAdapter(FrontdrawHttpEnvironment(client))
        result = adapter.run_once(
            task_dir=args.task_dir,
            run_id=args.run_id,
            agent_cmd=args.agent_cmd,
            work_dir=args.work_dir,
            image=args.image,
            agent_timeout_sec=args.agent_timeout_sec,
            verifier_timeout_sec=args.verifier_timeout_sec,
            run_verifier=not args.skip_verifier,
            keep_trial=args.keep_trial,
        )
        print(
            json.dumps(
                {
                    "create": dict(result.create),
                    "prepare": dict(result.prepare),
                    "agent_exec": dict(result.agent_exec) if result.agent_exec is not None else None,
                    "verifier_exec": dict(result.verifier_exec) if result.verifier_exec is not None else None,
                    "artifacts": dict(result.artifacts),
                    "downloaded_artifacts": dict(result.downloaded_artifacts),
                    "cleanup": dict(result.cleanup) if result.cleanup is not None else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
