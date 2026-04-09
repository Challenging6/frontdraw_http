# frontdraw-http

`frontdraw-http` 是 FrontDrawSkill-Bench 共享沙箱方案的本地代码骨架。

当前包含：

- `models.py`
  - `POST /trials`、`POST /prepare`、`POST /exec`、`GET /artifacts` 的最小数据模型
- `client.py`
  - 基于标准库 `urllib` 的 HTTP client
- `adapter.py`
  - 从已编译 task 目录读取 `assets/task.json`
  - 计算 `trial_hash`
  - 生成 create/prepare/exec 请求
  - 打包 task 目录为 `tar.gz`
- `environment.py`
  - 提供 environment-style 生命周期封装
  - `create_trial -> prepare_from_tarball -> exec_agent -> exec_verifier -> list/download_artifacts -> cleanup`
- `harbor_adapter.py`
  - Harbor `BaseEnvironment` 的薄桥接层
  - 把 Harbor 风格的 task instance 执行映射成：
    - `setup_run`
    - `package_task`
    - `prepare_run`
    - `run_agent`
    - `run_verifier`
    - `download_artifacts`
    - `cleanup_run`
- `server.py`
  - 最小可跑的共享沙箱服务
  - 已实现 `POST /trials`、`POST /prepare`、`POST /exec`
  - 已实现通用文件接口：`POST /upload-file`、`POST /upload-dir`、`GET /download-file`、`GET /download-dir`
  - 已实现 `GET /artifacts`、`GET /files/...`、`DELETE /trials`
- `harbor/harbor/src/harbor/environments/frontdraw_http.py`
  - 真实 Harbor `BaseEnvironment` 子类
  - 通过 `environment.import_path` 可被 Harbor 加载
  - 负责把 Harbor 约定的 `/logs/*`、`/tests`、`/solution`、`/skills` 映射到 trial workspace
- `cli.py`
  - `package-task`
  - `print-create-request`
  - `print-prepare-request`
  - `print-runtime`
  - `print-verifier-request`
  - `smoke-create`
  - `smoke-lifecycle`

当前边界：

- 这已经不只是前置散件，已经有一个可直接复用的 Harbor 风格桥接层和真实 `BaseEnvironment` 子类
- 仍然还没把这套类正式挂到你实际 Harbor 运行配置里
- 还没有实现 tarball 上传/内容寻址存储
- verifier 仍然是 stub

当前状态（2026-04-01）：

- 这套服务端逻辑已经通过 in-process `TestClient` 跑通过单实例生命周期 smoke
- 新版服务端已经补上 `trial` 磁盘持久化索引、内存 miss 回读、`GET /healthz`、exec 日志持久化与更稳的进程组终止
- 新版服务端已经通过“清空 `app.state.trials` 后再继续 `prepare / exec / artifacts / delete`”的回归 smoke
- Harbor 风格桥接层已经落地：`harbor_adapter.py`
- `run-adapter-inprocess` 已经跑通单题端到端：
  - `setup_run -> prepare_run -> run_agent -> list/download_artifacts -> cleanup`
- 真实 Harbor `BaseEnvironment` 子类已经落地：`harbor.environments.frontdraw_http:FrontdrawHttpEnvironment`
- 已补齐 Harbor 真环境最小能力需要的目录/文件传输接口：
  - `upload_file`
  - `upload_dir`
  - `download_file`
  - `download_dir`
- 已用 in-process `TestClient` 跑通过真实 Harbor 环境类最小 smoke：
  - `start -> upload_file -> exec -> download_dir -> download_file -> stop`
- 在当前开发容器里，直接打 `127.0.0.1:<port>` 会返回 `502`
- 因此当前推荐的验证方式是：
  - 本地先用 `TestClient`
  - 真正部署后再做远端 HTTP smoke

远端 redeploy 后，建议先测：

```bash
curl http://<host>/healthz
```

再测完整生命周期：

```bash
python3 -m harbor.frontdraw_http.cli smoke-lifecycle \
  --base-url http://<host> \
  --task-dir harbor-tasks/t1-main-data-v1/cat02_L3_23__svg__bundle \
  --run-id smoke01 \
  --tarball-url http://<host-or-storage>/cat02_L3_23__svg__bundle.tar.gz \
  --agent-cmd 'sh -lc "echo ok > submission/probe.txt"' \
  --keep-trial
```

启动服务：

```bash
uvicorn harbor.frontdraw_http.server:app --host 0.0.0.0 --port 8000
```

或直接：

```bash
python3 -m harbor.frontdraw_http.server
```

Docker 构建：

```bash
docker build -t frontdraw-http:dev harbor/frontdraw_http
docker run --rm -p 8000:8000 -v "$PWD/tmp/workspaces:/workspaces" frontdraw-http:dev
```

如果你习惯从仓库根目录用 `-f` 指定 Dockerfile，这种方式也支持：

```bash
docker build -f harbor/frontdraw_http/Dockerfile -t frontdraw-http:dev .
docker run --rm -p 8000:8000 -v "$PWD/tmp/workspaces:/workspaces" frontdraw-http:dev
```

如果你的构建平台**不支持本地 build context**，可以改用 GitHub 自拉版：

```bash
docker build -f harbor/frontdraw_http/Dockerfile.github -t frontdraw-http:dev .
```

这个版本会在构建阶段自动 clone 默认仓库：

- `https://github.com/Challenging6/frontdraw_http.git`
- 分支：`main`
- 代码目录：仓库根目录

如果你的平台只能直接粘贴 Dockerfile 内容，也可以直接把 `Dockerfile.github` 的内容贴进去，不需要额外传参。

如果以后你把代码放进了仓库子目录，再手动把文件顶部这三个环境变量改掉即可：

```bash
FRONTDRAW_GIT_REPO=...
FRONTDRAW_GIT_REF=...
FRONTDRAW_GIT_SOURCE_SUBDIR=harbor/frontdraw_http
```

最小使用方式：

```bash
python3 -m harbor.frontdraw_http.cli print-create-request \
  --task-dir harbor-tasks/t1-main-data-v1/cat02_L3_23__svg__bundle \
  --run-id smoke01
```

```bash
python3 -m harbor.frontdraw_http.cli package-task \
  --task-dir harbor-tasks/t1-main-data-v1/cat02_L3_23__svg__bundle \
  --output /tmp/cat02_L3_23__svg__bundle.tar.gz
```

```bash
python3 -m harbor.frontdraw_http.cli print-verifier-request \
  --task-dir harbor-tasks/t1-main-data-v1/cat02_L3_23__svg__bundle \
  --workspace-root /workspaces/demo123
```

真实 Harbor 接入时，推荐使用：

```toml
[environment]
import_path = "harbor.environments.frontdraw_http:FrontdrawHttpEnvironment"
```
