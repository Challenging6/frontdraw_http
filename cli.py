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
                    "environment": dict(runtime.environment),
                    "agent": dict(runtime.agent),
                    "verifier": dict(runtime.verifier),
                    "skills": dict(runtime.skills),
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

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
