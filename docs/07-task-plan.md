# 分阶段任务清单与实施计划

## 1. 执行约定

- 每个阶段都是一个可独立验收的单元；完成后执行「构建 → 测试 → 冒烟」，通过后提交一次中文 Git commit 并推送。
- 当前仓库已配置远端 `origin`（`https://github.com/linqin-cn/ToPDF.git`），分支 `master`。
- 我默认在 `master` 上直接推进（与现有仓库习惯一致）；若你希望走分支 + PR，请告知，我会改用 `codex/` 前缀的功能分支。
- 任何需要你操作的环节（密钥、远端权限、部署环境、镜像仓库）我会立即暂停并列出清单。
- **推送由你手动执行**：我只做本地中文提交（`git commit`），不运行 `git push`。每个阶段结束时我会给出 commit 哈希与本阶段摘要，由你决定何时推送。

## 2. 阶段划分

### P0 工程化基线（无业务行为变更）

目标：把单文件 demo 变成可维护、可测试、可 CI 的工程骨架。

产出：

1. `pyproject.toml` + `uv.lock`（依赖、ruff、mypy、pytest 配置），并由 uv 导出 `requirements.txt` 保持 pip 可用。
2. 目录重构为 `src/resume_pdf/` 分层结构；入口改为 `create_app()` 工厂 + lifespan。
3. 补齐根 `.gitignore`（虚拟环境、缓存、产物、`.env`）。
4. 契约工具链：`scripts/validate_openapi.py`（已具备）+ `scripts/build_agent_spec.py`（从完整契约生成星辰平台导入用的精简契约），并在 CI 中校验「生成结果与仓库文件一致」。
5. `.github/workflows/ci.yml`：静态检查 + 契约校验 + 单元测试（此阶段暂不含容器任务）。

验收：`ruff check`、`ruff format --check`、`mypy` 通过；单元测试骨架可运行；服务在容器内 `/healthz` 正常。

提交：`chore: 搭建工程化基线（依赖锁定、目录分层、质量工具与 CI 骨架）`

### P1 配置、日志与异常体系

目标：建立横切基础能力，后续所有模块都依赖它。

产出：

1. `core/config.py`：`RPR_` 前缀配置中心，含跨字段校验与启动失败策略。
2. `core/logging.py`：JSON 结构化日志，敏感字段过滤（不落 HTML 正文）。
3. `core/errors.py` + `api/exception_handlers.py`：错误码枚举、领域异常、三个全局处理器。
4. `core/request_id.py`：链路 ID 中间件（回显、生成、校验）。
5. `api/middleware.py`：请求体大小限制与访问日志。

验收：单元测试覆盖配置校验与错误码映射；容器内构造多种非法请求，响应结构与状态码完全符合契约；日志为 JSON 且无正文泄漏。

提交：`feat: 新增配置中心、结构化日志与统一异常处理`

### P2 渲染引擎与资源隔离

目标：核心能力落地，这是全案风险最高的一段。

产出：

1. `render/engine.py`：`RenderEngine` 端口、`RenderOptions`、`RenderResult`（类型上杜绝 HTML 改写）。
2. `render/chromium.py`：Playwright 实现——常驻浏览器、每请求独立 context、路由拦截（仅放行保留域名的文档请求）、`set_offline(True)`、关闭 JS 与服务工作者、`page.pdf(...)` 页面参数、三层超时、失败自愈与按计数回收。
3. `render/pagesize.py`：尺寸与边距解析、单位换算、范围校验、`@page` 优先级判定。
4. `render/service.py`：`RenderService` 编排（并发闸门、排队策略、页数与体积闸门、临时目录生命周期）。
5. 测试：真实 PDF 渲染的集成用例 + 安全用例（SSRF、本地文件、脚本执行）。

验收：黄金样例集合渲染通过；SSRF 与本地文件读取全部被阻断；超时用例无残留进程与临时文件；`html` 字节一致性断言通过。

提交：`feat: 实现 Chromium 渲染引擎与渲染期资源隔离`

### P3 接口层与调用限制

目标：对外契约成型。

产出：

1. `api/routers/render.py`：`POST /v1/render`（JSON 与表单两种入参、JSON 与二进制两种响应）+ 旧 `POST /render` 兼容层。
2. `core/ratelimit.py`：令牌桶限流（按 Key 或 IP）+ 并发闸门与队列策略。
3. 鉴权依赖：`X-API-Key` 与 Bearer，常量时间比较；`AUTH_ENABLED` 开关。
4. 移除旧的 `/files/*` 静态目录挂载，改为受控下载端点。
5. 契约端点：`/v1/openapi.yaml` 与 `/v1/openapi.agent.yaml`，运行时按 `PUBLIC_BASE_URL` 注入 `servers`。

验收：API 用例（`06-test-and-acceptance.md` 4.1 节）全部通过；压测可复现 429/503 行为；旧接口回归用例通过。

提交：`feat: 交付 v1 渲染接口、限流鉴权与旧接口兼容层`

### P4 产物存储与清理

目标：产物生命周期可控。

产出：

1. `storage/base.py` + `storage/local.py`：`ArtifactStore` 端口与本地实现（不可枚举 ID、按日期分目录）。
2. `api/routers/files.py`：下载端点（ETag、`If-None-Match`、RFC 5987 文件名、`key` 与 `signed` 两种访问模式）。
3. `storage/cleanup.py`：TTL 清理后台任务、启动时遗留清理、leader-only 开关。

验收：下载用例、410/404 分支、签名校验用例通过；清理任务在测试中可被显式触发并验证目录变化。

提交：`feat: 新增产物存储、受控下载端点与 TTL 清理`

### P5 可观测性

目标：线上可诊断。

产出：

1. `observability/metrics.py`：`05-deployment-ops.md` 第 6 节全部指标 + `/metrics` 端点（含 token 保护）。
2. 访问日志补齐排队与渲染分段耗时及 `error_code`；`html_sha256_12` 落日志。
3. `observability/tracing.py`：OpenTelemetry 开关式接入（默认关闭）。
4. `/healthz`、`/readyz` 实现（含引擎与存储检查）。

验收：可观测性用例（4.5 节）通过；`/metrics` 指标名与文档一致；关闭开关时行为与零开销路径正确。

提交：`feat: 补齐健康检查、Prometheus 指标与可选链路追踪`

### P6 容器化与部署交付

目标：一键可部署、可复现。

产出：

1. 多阶段 `Dockerfile`（builder + runtime、非 root、Chromium 与 CJK 字体、`HEALTHCHECK /readyz`）。
2. `deploy/docker-compose.yml`、`deploy/.env.example`、资源限额样例。
3. `scripts/smoke.ps1`（Windows）与 `scripts/smoke.sh`（Linux 与容器内）冒烟脚本。
4. README 重写为运维可用版本（启动、配置、接口示例、故障排查）。

验收：`docker build` 成功；容器以只读根、非 root、限额启动后冒烟全绿；`/readyz` 在浏览器就绪前 503、就绪后 200。

提交：`build: 交付多阶段镜像、部署样例与冒烟脚本`

### P7 性能、安全与验收收尾

目标：把「生产级」落到证据上。

产出：

1. `scripts/loadtest.py`：并发与时延压测（含限流与超时场景）。
2. 保真度评估脚本（截图与 PDF 栅格化做 SSIM 比对）与结果。
3. `docs/08-test-report.md`（真实执行输出）、`docs/09-acceptance-report.md`（逐条对应验收标准）。
4. 契约最终对齐：OpenAPI、文档与实现一致性复查。

验收：第 `06` 号文档第 7 节的 11 条验收标准逐条给出证据；压测与保真报告可复现。

提交：`test: 完成性能、保真与安全验收并补充验收报告`

## 3. 里程碑

| 里程碑 | 包含阶段 | 交付意义 |
| --- | --- | --- |
| M1 可维护 | P0–P1 | 工程骨架与横切能力就绪 |
| M2 可渲染 | P2 | 核心渲染与隔离能力可用（风险最大节点） |
| M3 可调用 | P3–P4 | 对外契约与产物生命周期闭环 |
| M4 可运维 | P5–P6 | 可观测与可部署 |
| M5 可验收 | P7 | 证据齐全，可交付 |

## 4. 需要你配合的事项（遇到即暂停）

| 编号 | 事项 | 何时需要 |
| --- | --- | --- |
| X1 | 推送：已确认由你手动执行 `git push`，我只做本地提交 | 已确认 |
| X2 | GitHub Actions 是否可用（私有仓库额度）；若受限则改用本地脚本做门禁 | P0 |
| X3 | API Key 的生成与保管方式（我生成随机值写入本机 `.env`，密钥不入库） | P3 |
| X4 | `SIGNING_SECRET`（至少 32 字符）由你提供或我生成后你保存 | P4 |
| X5 | 拉取 Playwright 与 apt 依赖需要外网，我会在首次构建前申请一次性授权 | P2 首次构建 |
| X6 | 部署域名分配后请告知，我把它写入 `PUBLIC_BASE_URL` 并重新验证 `/v1/openapi.agent.yaml` | P6 |

## 5. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| Chromium 镜像体积大、首次构建慢 | 交付节奏 | 多阶段构建 + 依赖层缓存；必要时在 P2 前先做一次镜像体积验证 |
| 渲染保真度不达标（特定 CSS 特性） | 核心承诺 | P2 结束即以黄金样例集合验证；不达标时在文档中如实记录限制，而非改 HTML 掩盖 |
| 引擎进程泄漏 | 稳定性 | 常驻实例 + 超时硬杀 + 按计数回收 + 容器 `pids_limit` 兜底 |
| 沙箱取舍不符合合规要求 | 上线阻塞 | 已在文档中显式说明，替代方案（加 `CAP_SYS_ADMIN` 或 gVisor/Kata）待你决策 |
| 契约与实现漂移 | 维护成本 | OpenAPI 作为权威契约纳入 CI 校验；集成测试断言响应结构 |

## 6. 实施顺序总览

```
P0 工程化基线 → P1 横切能力 → P2 渲染引擎 → P3 接口层 → P4 存储清理
   → P5 可观测性 → P6 容器与部署 → P7 验收收尾
```

每个阶段结束后我会给出：本阶段变更摘要、构建与测试的真实输出、冒烟结果、commit 哈希与推送状态，再进入下一阶段。
