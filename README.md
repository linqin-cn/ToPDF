# Resume PDF Renderer

把智能体提交的完整 HTML（含内联 CSS）原样渲染为简历样式 PDF 的服务。

核心约束：不修改、不补全、不重写、不美化输入 HTML 的结构、内容与样式；渲染期不执行
JavaScript，默认零外部网络（外链字体、图片与样式不会被加载，资源须内联为 data URI）。

## 当前进度

功能已闭环，可作内网工具使用：渲染（`POST /v1/render`、旧 `POST /render`）、下载
（`GET /v1/files/{file_id}`）、鉴权、限流、并发闸门、产物 TTL 清理、健康检查与基础指标。
交付目标与阶段取舍见 docs/07-task-plan.md 第 0 节。

## 开发文档

全部设计与计划文档见 docs/README.md，其中：

- 接口契约与错误码：docs/03-api-contract.md
- OpenAPI 3.0.3 契约：docs/04-openapi.yaml（完整版）
- 平台导入用精简契约：docs/04-openapi.agent.yaml
- 部署与运维：docs/05-deployment-ops.md

## 本地开发

需要 Python 3.13 与 uv（见 https://docs.astral.sh/uv/）。

```bash
uv sync                 # 安装依赖，含 dev 组
uv run pytest           # 单元测试
uv run ruff check .     # Lint
uv run ruff format --check .
uv run mypy             # 类型检查
python scripts/validate_openapi.py
python scripts/build_agent_spec.py --check
```

本地启动：

```bash
export RPR_API_KEYS=dev-key        # 默认开启鉴权，必须配置密钥；纯内网可设 RPR_AUTH_ENABLED=false
export RPR_ARTIFACT_DIR=./artifacts
uv run uvicorn resume_pdf.main:create_app --factory --app-dir src --host 127.0.0.1 --port 8000
uv run python scripts/smoke_local.py http://127.0.0.1:8000   # 冒烟：渲染 + 下载校验
```

渲染需要浏览器：本地首次运行先执行 `uv run playwright install chromium`。

## 容器

```bash
docker build -t resume-pdf-renderer:dev .
docker run --rm -p 8000:8000 -e RPR_API_KEYS=dev-key resume-pdf-renderer:dev
```

生产级镜像（多阶段构建、非 root、Chromium 与中文字体、资源限额）在 P6 阶段交付，
配置项清单见 docs/05-deployment-ops.md。
