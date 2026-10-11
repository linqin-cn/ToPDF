# 架构设计

## 1. 总体形态

单容器、单进程、无状态的同步渲染服务。分层为「接口层 → 应用层 → 渲染/存储端口 → 基础设施实现」，外部契约稳定，内部实现（渲染引擎、存储后端）可替换。

```
调用方（智能体 / 星辰工作流）
        │  HTTP/JSON 或 multipart
        ▼
┌───────────────────────────── 容器 ─────────────────────────────┐
│  中间件：request_id · 体积限制 · 鉴权 · 限流 · 访问日志        │
│  ────────────────────────────────────────────────────────────  │
│  路由层：/v1/render · /render(兼容) · /v1/files/{id}           │
│          /healthz · /readyz · /metrics                         │
│  ────────────────────────────────────────────────────────────  │
│  应用层：RenderService（参数归一化 · 并发闸门 · 超时 · 页数校验）│
│  ────────────────────────────────────────────────────────────  │
│  端口层：RenderEngine（渲染）      ArtifactStore（产物）        │
│  ────────────────────────────────────────────────────────────  │
│  实现层：ChromiumEngine / WeasyPrintEngine   LocalStore        │
│          Chromium 子进程（沙箱外隔离边界 = 容器）              │
└────────────────────────────────────────────────────────────────┘
        │                              │
   临时目录（请求级，即用即删）    产物目录（TTL 清理 / 可换对象存储）
```

## 2. 目标目录结构

```
resume-pdf-renderer/
├── src/resume_pdf/
│   ├── main.py                     # create_app() + lifespan（启动/优雅关闭）
│   ├── api/
│   │   ├── deps.py                 # 依赖注入：配置、引擎、存储、限流器
│   │   ├── schemas.py              # Pydantic 请求/响应模型
│   │   ├── middleware.py           # request_id / 体积限制 / 访问日志
│   │   ├── exception_handlers.py   # 全局异常 → 统一错误码
│   │   └── routers/
│   │       ├── render.py           # 渲染接口（新旧两版）
│   │       ├── files.py            # 产物下载
│   │       └── ops.py              # 健康检查与指标
│   ├── core/
│   │   ├── config.py               # pydantic-settings 配置中心
│   │   ├── constants.py            # 具名常量与枚举（无魔法值）
│   │   ├── errors.py               # 领域异常基类与错误码枚举
│   │   ├── logging.py              # 结构化日志
│   │   ├── request_id.py           # 链路 ID 生成与上下文传递
│   │   └── ratelimit.py            # 令牌桶 + 并发闸门
│   ├── render/
│   │   ├── engine.py               # RenderEngine 端口 + RenderOptions/Result
│   │   ├── chromium.py             # Playwright 实现 + 浏览器监管池
│   │   ├── weasyprint_engine.py    # 备选实现（可插拔，默认不启用）
│   │   ├── pagesize.py             # 页面尺寸/边距解析与校验
│   │   └── service.py              # RenderService：编排与资源生命周期
│   ├── storage/
│   │   ├── base.py                 # ArtifactStore 端口
│   │   ├── local.py                # 本地目录实现
│   │   └── cleanup.py              # TTL 清理后台任务
│   └── observability/
│       ├── metrics.py              # Prometheus 指标
│       └── tracing.py              # OpenTelemetry（开关式）
├── tests/{unit,integration,smoke,fixtures}/
├── scripts/                        # 冒烟脚本、并发压测脚本
├── deploy/                         # compose、.env.example、资源限额样例
├── docs/                           # 本套开发文档
├── pyproject.toml + uv.lock        # 依赖与工具链唯一事实来源
├── requirements.txt                # 由 uv 导出，兼容 pip 环境
├── Dockerfile
└── .github/workflows/ci.yml
```

## 3. 渲染引擎设计

### 3.1 端口定义

```python
class RenderEngine(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def healthy(self) -> bool: ...
    async def render(self, html: str, options: RenderOptions) -> RenderResult: ...
```

- `RenderOptions`：`page_size`、`orientation`、`margin`、`media`、`print_background`、`timeout_ms`、`max_pages`。
- `RenderResult`：`pdf_path`、`page_count`、`byte_size`、`duration_ms`、`engine`、`engine_version`。
- 契约中**不存在**任何可写 HTML 的参数（无 `extra_css`、无 `scripts`、无 `transform`），从类型层面保证 R2。

### 3.2 Chromium 实现（默认）

**浏览器生命周期**：lifespan 启动时拉起 1 个常驻 `Browser`（`chromium.launch`），进程内单例；每个请求 `new_context()` → `new_page()` → 渲染 → 关闭 context。渲染计数达到 `BROWSER_RECYCLE_AFTER`（默认 200）后主动回收重启，抑制长跑进程的内存增长。

**监管**：任何渲染异常/超时若触达浏览器进程，标记为不可信 → 关闭并重建，`engine_restarts_total` 计数。重建失败进入降级状态，`/readyz` 返回 503。

**零网络 + 保留 base URL 的取巧做法**：不直接把 HTML 交给 `set_content`（会丢失 base URL 语义，影响相对路径与 CSS 语义），而是让页面 `goto` 一个保留域名（如 `https://resume.local/`），同时用 `context.route()` 拦截**全部**请求：仅对该保留域名的文档请求用 `fulfill(body=html)` 返回原始 HTML，其余一律 `abort()`。这样：
- 智能体的 HTML 未经任何改写即成为文档主体；
- 相对 URL 有确定的 base，与浏览器语义一致；
- 网络出口在路由层被切断，且叠加 `context.set_offline(True)` 双保险。

**禁用项**：`java_script_enabled=False`、`service_workers="block"`、`accept_downloads=False`、不注入任何 style/script。

**页面参数（不改 HTML 的唯一合法途径）**：全部经 `page.pdf(...)` 选项下达——`width/height`、`margin`、`printBackground`、`preferCSSPageSize`、`scale`。禁止 `addStyleTag` 与任何 `evaluate` 形式的 DOM 变更。

**@page 优先级（核心保真规则）**

| 调用方入参 | 引擎设置 | 生效尺寸 |
| --- | --- | --- |
| 未传任何页面参数 | `preferCSSPageSize=true` | HTML 内 `@page` 为准；无 `@page` 回退 `DEFAULT_PAGE_SIZE`（A4 竖版） |
| 传了 `size`/`orientation`/`margin` 任一 | `preferCSSPageSize=false` + 显式 `width/height/margin` | 显式入参为准（引擎级覆盖，HTML 未被改动） |

**媒体类型**：默认 `print`（`page.emulate_media(media="print")`），符合「PDF 即印刷产物」语义，尊重 `@media print`；允许调用方显式传 `media="screen"` 以适配按屏幕稿撰写的 HTML。

**超时**：三层——导航/装载超时、`page.pdf` 超时、以及外层 `asyncio.wait_for` 的硬超时（默认 20s，上限由 `RENDER_MAX_TIMEOUT_MS` 钳制）。硬超时触发即销毁 context，必要时重建浏览器，确保不留僵尸。

### 3.3 WeasyPrint 备选实现

与 Chromium 实现同一端口、同一行为契约，仅用于「镜像体积敏感 / 需要纯 Python 部署」的场景，通过 `RENDER_ENGINE=weasyprint` 切换。资源隔离依靠自定义 `url_fetcher`（默认拒绝一切非 `data:` 请求）与 `base_url` 指向空目录。**注意**：本机 Windows 无法运行该实现，验证只在容器内进行。

## 4. 并发与资源模型

| 机制 | 设计 |
| --- | --- |
| 并发闸门 | `asyncio.Semaphore(MAX_CONCURRENT_RENDERS)`，默认 4；闸门在请求进入渲染前获取，`finally` 释放 |
| 排队策略 | `MAX_QUEUE_SIZE`（默认 8）：未满则等待（计入超时预算），满则按 `QUEUE_FULL_STRATEGY` 返回 `429 RATE_LIMITED` 或 `503 SERVICE_BUSY` |
| 渲染超时 | 硬超时 `asyncio.wait_for`；排队等待时间独立计时，避免「排队把渲染预算吃光」 |
| 请求体大小 | 中间件双重限制：`Content-Length` 预检 + 流式累计上限；超限 `413 PAYLOAD_TOO_LARGE` |
| HTML 上限 | 默认 2 MiB，硬上限 10 MiB（`MAX_HTML_BYTES` 不得超过硬上限，配置加载时校验） |
| 输出上限 | `MAX_PDF_BYTES`（默认 20 MiB）与 `MAX_PAGES`（默认 20）双闸：任一超限 → `RENDER_OUTPUT_TOO_LARGE` / `PDF_TOO_MANY_PAGES` |
| 进程限额 | 容器 `--memory`、`--cpus`、`--pids-limit`、`--read-only` + `tmpfs`；`--cap-drop=ALL`、`no-new-privileges` |
| 隔离边界说明 | 容器内以非 root 运行并关闭 Chromium 自带沙箱（`chromium_sandbox=False`），因为容器本身即隔离边界：无网络、只读根文件系统、能力集清空、PID 与内存受限。此取舍在 `05-deployment-ops.md` 中给出显式理由与替代方案 |
| 事件循环保护 | 渲染全程异步；任何同步阻塞操作（PDF 页数解析、文件写入）统一下沉到 `anyio.to_thread`，不得阻塞事件循环（这是对现有 `app.py` 的主要修正） |

## 5. 存储与产物生命周期

- 端口 `ArtifactStore`：`put(content: bytes, *, content_type, ttl) -> ArtifactRef` / `open(ref)` / `delete(ref)` / `sweep()`。
- 本地实现：产物写入 `${ARTIFACT_DIR}/{yyyyMMdd}/{token}.pdf`，`token = secrets.token_urlsafe(16)`（不可枚举）。
- 下载：`GET /v1/files/{token}`，默认要求 API Key；可选签名模式 `DOWNLOAD_URL_MODE=signed`，使用 HMAC-SHA256 生成短期下载链接（`exp` + `sig`），便于把链接直接交给浏览器。
- 清理：后台任务每 `CLEANUP_INTERVAL_SECONDS`（默认 300）扫描一次，删除超过 `ARTIFACT_TTL_SECONDS`（默认 3600）的产物；启动时执行一次遗留清理，覆盖进程被杀导致的残留。
- 扩展：切对象存储只需新增 `ArtifactStore` 实现，接口与错误码不变。

## 6. 安全模型（渲染期）

| 威胁 | 对策 |
| --- | --- |
| SSRF（HTML 内 `<img src=内网地址>`） | 路由层全量 `abort` + `set_offline(True)`；有专门测试用例（含 `169.254.169.254`、`127.0.0.1`、`file://`） |
| 读取宿主文件（`file:///etc/passwd`、`<iframe src>`） | 仅放行保留域名的文档请求，其余 scheme/host 一律 abort |
| XSS / 脚本执行 | `java_script_enabled=False`，不注入脚本 |
| 巨量 HTML / PDF 炸弹 | 体积上限、页数上限、输出字节上限、渲染硬超时 |
| 资源耗尽 | 并发闸门、队列上限、容器 `memory/cpus/pids` 限额 |
| 信息泄漏 | 对外错误只含稳定错误码与安全措辞；日志不记录 HTML 正文（记 `sha256` 前 12 位与字节数） |
| 未授权访问 | `X-API-Key`（或 Bearer）鉴权，`constant-time` 比较；可显式关闭用于纯内网 |

## 7. 配置模型

`pydantic-settings` 统一承载，环境变量前缀 `RPR_`（保留现有 `OUTPUT_DIR`、`PUBLIC_BASE_URL` 作为兼容别名）。启动时全量校验：类型、取值范围、跨字段约束（如 `MAX_HTML_BYTES <= HARD_MAX_HTML_BYTES`），非法配置**直接启动失败**而非运行期才报错。完整变量表见 `05-deployment-ops.md`。

## 8. 错误处理模型

```
AppError(code, http_status, public_message, details)
 ├─ ValidationError / UnsupportedMediaTypeError / PayloadTooLargeError
 ├─ UnauthorizedError / NotFoundError
 ├─ RateLimitedError / ServiceBusyError
 ├─ RenderTimeoutError / RenderFailedError / RenderOutputTooLargeError
 └─ InternalError
```

注册三个处理器：`AppError`（按错误码映射状态码与响应体）、`RequestValidationError`（Pydantic → `INVALID_ARGUMENT`，附字段级 `details`）、`Exception`（兜底 → `INTERNAL_ERROR`，记录堆栈但**不回显**）。快速失败原则：参数问题绝不进入渲染阶段。

## 9. 与旧接口的兼容策略

- `POST /render` 保留原请求体（`html` + `file_name`）与原响应字段（`success`、`file_name`、`file_url`、`message`），另**追加** `request_id`、`page_count`、`duration_ms`，并补齐错误码（错误响应在原结构上追加 `code`/`request_id`，保证既有解析不炸）。
- 新能力走 `POST /v1/render`，参数与错误码完整体现在 OpenAPI 中。
- 旧接口标记 `deprecated: true`，`Sunset` 计划在 v2 提出；是否删除由你决定（决策点 D3/待确认信息 4）。

## 10. 技术选型汇总（建议值）

| 领域 | 选型 | 说明 |
| --- | --- | --- |
| 语言/运行时 | Python 3.13 | 与本地环境一致，消除漂移 |
| Web | FastAPI + Uvicorn | 沿用现状 |
| 校验/配置 | Pydantic v2 + pydantic-settings | 沿用现状 |
| 渲染 | Playwright（Chromium headless） | 高保真 + 可关 JS + 可断网 |
| 依赖管理 | uv（`pyproject.toml` + `uv.lock`） | 本机已装 `uv`，支持可复现构建 |
| 质量工具 | ruff（lint + format）、mypy、pytest、pytest-asyncio、httpx | 全部纳入 CI |
| 日志/指标 | 标准库 logging（JSON 格式）+ prometheus-client | 零外部依赖，易接入 |
| 追踪 | OpenTelemetry（开关，默认关） | 可选接入 |
| PDF 校验 | pypdf | 测试期读取页数与元信息 |
