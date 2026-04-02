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
- `server.py`
  - 最小可跑的共享沙箱服务
  - 已实现 `POST /trials`、`POST /prepare`、`POST /exec`、`GET /artifacts`、`GET /files/...`、`DELETE /trials`
- `cli.py`
  - `package-task`
  - `print-create-request`
  - `print-prepare-request`
  - `print-runtime`
  - `print-verifier-request`
  - `smoke-create`
  - `smoke-lifecycle`

当前边界：

- 这是 Harbor adapter 的前置代码，不是 Harbor 核心插件本体
- 还没有真正接到 Harbor 的 `BaseEnvironment`
- 还没有实现 tarball 上传/内容寻址存储
- verifier 仍然是 stub

当前状态（2026-04-01）：

- 这套服务端逻辑已经通过 in-process `TestClient` 跑通过单实例生命周期 smoke
- 在当前开发容器里，直接打 `127.0.0.1:<port>` 会返回 `502`
- 因此当前推荐的验证方式是：
  - 本地先用 `TestClient`
  - 真正部署后再做远端 HTTP smoke

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
docker build \
  -f harbor/frontdraw_http/Dockerfile.github \
  --build-arg GIT_REPO=https://github.com/<owner>/<repo>.git \
  --build-arg GIT_REF=main \
  -t frontdraw-http:dev .
```

这个版本会在构建阶段自动 clone 指定仓库，并只取 `harbor/frontdraw_http/` 下的服务代码。

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
