# 接口契约

基础约定

- 传输：HTTP/1.1 或 HTTP/2，UTF-8 编码，所有时间戳为 RFC 3339（UTC）。
- 鉴权：默认启用，请求头 `X-API-Key: <key>`（亦接受 `Authorization: Bearer <key>`）；`AUTH_ENABLED=false` 时忽略。
- 链路：调用方可传 `X-Request-Id`（≤64 字符，`[A-Za-z0-9._-]`），服务端回显；未传则服务端生成。所有响应都会带 `X-Request-Id`。
- 输入来源：**仅接受内联 HTML 字符串**。服务端不抓取任何 URL，不接受 DOCX/Markdown/URL 作为输入源。
- 资源内联要求：渲染期默认零网络，HTML 内的相对路径资源、外链字体、外链图片**不会**被加载。调用方必须把字体/图片内联为 `data:` URI 或内联样式。（这是安全设计，不是缺陷；见 `02-architecture.md` 第 6 节。）

## 1. `POST /v1/render`

将完整 HTML 原样渲染为 PDF。

### 1.1 请求头

| 头 | 必填 | 说明 |
| --- | --- | --- |
| `Content-Type` | 是 | `application/json`（推荐）；兼容 `application/x-www-form-urlencoded`、`multipart/form-data` |
| `Accept` | 否 | `application/json`（默认）或 `application/pdf`；与服务端 `RESPONSE_MODE` 共同决定响应形态，`Accept: application/pdf` 优先级更高 |
| `X-API-Key` | 取决于配置 | 鉴权密钥 |
| `X-Request-Id` | 否 | 自定义链路 ID |

### 1.2 请求体（JSON）

| 字段 | 类型 | 必填 | 默认 | 约束 |
| --- | --- | --- | --- | --- |
| `html` | string | 是 | - | 非空（去空白后），UTF-8 字节数 ≤ `MAX_HTML_BYTES`（默认 2 MiB，硬上限 10 MiB） |
| `file_name` | string | 否 | `resume.pdf` | ≤128 字符；经白名单净化（保留中英文、数字、`._-`），自动补 `.pdf`；净化后为空则回落 `resume.pdf` |
| `page.size` | string | 否 | 不传则由 HTML `@page` 决定，缺省回退 `A4` | `A4`、`A3`、`A5`、`Letter`、`Legal`、`Tabloid`、`Custom`；取 `Custom` 时必须同时给 `page.width` 与 `page.height` |
| `page.width` / `page.height` | number | 否 | - | 仅 `page.size=Custom` 时生效，取值 10–2000 |
| `page.unit` | string | 否 | `mm` | `px`、`mm`、`cm`、`in`、`pt`；作用于 `width`、`height` 与数字型边距 |
| `page.orientation` | string | 否 | `portrait` | `portrait` \| `landscape` |
| `page.margin` | string | 否 | `0` | CSS 风格简写：`0`、`12mm`、`10mm 15mm`、`5mm 10mm 5mm 10mm`；裸数字按 `page.unit` 解释；单项上限 50mm |
| `page.media` | string | 否 | `print` | `print` \| `screen` |
| `page.print_background` | boolean | 否 | `true` | 关闭会导致背景色/底色丢失，非必要勿关 |
| `timeout_ms` | integer | 否 | `RENDER_TIMEOUT_MS`（20000） | 1000 – `RENDER_MAX_TIMEOUT_MS`（30000） |
| `max_pages` | integer | 否 | `MAX_PAGES`（5） | 1 – `HARD_MAX_PAGES`（100） |
| `response_mode` | string | 否 | `RESPONSE_MODE`（`url`） | `url` \| `binary`；被 `Accept` 头覆盖 |
| `artifact_ttl_seconds` | integer | 否 | `ARTIFACT_TTL_SECONDS`（3600） | 60 – `HARD_MAX_TTL_SECONDS`（604800），仅 `response_mode=url` 有意义 |

**表单/多部分提交的扁平键**（兼容旧调用方）：`html`、`file_name`、`page_size`、`page_orientation`、`page_margin`、`page_media`、`page_print_background`、`timeout_ms`、`max_pages`、`response_mode`。与 JSON 字段语义一致。

### 1.3 页面参数优先级

1. 未传任何 `page.*` → 尊重 HTML 内 `@page`（`preferCSSPageSize=true`）；无 `@page` 时用 `DEFAULT_PAGE_SIZE`。
2. 传了 `page.size` / `orientation` / `margin` 任一 → 以入参为准，覆盖 HTML `@page` 的尺寸与边距。
3. 任何情况下都**不修改 HTML**：参数通过渲染引擎选项下达，不注入 `<style>` 或其它节点。

### 1.4 响应：`response_mode=url`（默认）

`200 OK`，`Content-Type: application/json`

```json
{
  "success": true,
  "request_id": "b7d1f0c2a1e34f8f9a1c2d3e4f5a6b7c",
  "file_id": "Kf3nQ7xT2pLmZaVq1sB0dA",
  "file_name": "张三-Java后端实习生.pdf",
  "file_url": "https://pdf.example.com/v1/files/Kf3nQ7xT2pLmZaVq1sB0dA",
  "expires_at": "2026-10-11T13:30:00Z",
  "page_count": 1,
  "size_bytes": 48321,
  "duration_ms": 812,
  "engine": "chromium"
}
```

### 1.5 响应：`response_mode=binary` / `Accept: application/pdf`

`200 OK`，`Content-Type: application/pdf`，二进制流（不落盘）。

| 响应头 | 说明 |
| --- | --- |
| `Content-Disposition` | `attachment; filename="resume.pdf"; filename*=UTF-8''%E5%BC%A0%E4%B8%89.pdf`（RFC 5987，中文名安全） |
| `Content-Length` | PDF 字节数 |
| `X-Page-Count` | 页数 |
| `X-Render-Duration-Ms` | 渲染耗时 |
| `X-Engine` | 渲染引擎标识 |

失败时**仍返回 JSON 错误体**（便于排障），只是 HTTP 状态码与 `code` 表达原因。

### 1.6 示例

```bash
curl -X POST https://pdf.example.com/v1/render \
  -H "X-API-Key: $RPR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "html": "<html><head><style>@page{size:A4;margin:12mm} body{font-family:\"Noto Sans CJK SC\"}</style></head><body><h1>张三</h1></body></html>",
    "file_name": "张三-简历.pdf",
    "page": {"size": "A4", "margin": "12mm", "media": "print"},
    "timeout_ms": 20000
  }'
```

```bash
# 直接拿二进制
curl -X POST https://pdf.example.com/v1/render \
  -H "X-API-Key: $RPR_KEY" -H "Accept: application/pdf" \
  -H "Content-Type: application/json" \
  -d '{"html":"<h1>张三</h1>"}' -o 张三.pdf
```

## 2. `GET /v1/files/{file_id}`

下载已生成产物。

- 鉴权：`DOWNLOAD_URL_MODE=key` 时需 `X-API-Key`；`signed` 模式需查询参数 `exp` + `sig`（HMAC-SHA256）。
- 响应头：`Content-Type: application/pdf`、`Content-Disposition`（RFC 5987）、`ETag`（内容 SHA-256 前 16 位）、`Cache-Control: private, max-age=0, must-revalidate`。
- 支持 `If-None-Match` → `304`。
- 过期产物返回 `410 FILE_EXPIRED`；不存在返回 `404 FILE_NOT_FOUND`。

## 3. 运维端点

| 端点 | 语义 | 响应 |
| --- | --- | --- |
| `GET /healthz` | 存活探针，进程在即 200 | `{"status":"ok","version":"1.0.0","uptime_s":1234}` |
| `GET /readyz` | 就绪探针：渲染引擎可用且产物目录可写 | 200 `{"status":"ok","checks":{"engine":"ok","storage":"ok"}}`；否则 503 `{"status":"degraded",...}` |
| `GET /metrics` | Prometheus 文本格式 | 指标见 `05-deployment-ops.md`；`METRICS_ENABLED=false` 时返回 404 |
| `GET /v1/openapi.yaml` | 完整 OpenAPI 3.0.3 契约（权威版本），`servers[0].url` 按 `PUBLIC_BASE_URL` 运行时注入 | `application/yaml` |
| `GET /v1/openapi.agent.yaml` | 面向星辰平台导入的精简契约（仅渲染与下载），由完整契约生成 | `application/yaml` |
| `GET /docs`、`/redoc` | 交互式文档（由 FastAPI 生成） | HTML |

## 4. 兼容接口 `POST /render`（已弃用）

保留现有调用方可用，语义等价于 `POST /v1/render` 且 `response_mode=url`、页面参数取默认。

请求：`html`、`file_name`（与旧版一致）。

成功响应（旧字段全部保留，仅追加新字段）：

```json
{
  "success": true,
  "file_name": "张三-Java后端实习生.pdf",
  "file_url": "https://pdf.example.com/v1/files/Kf3nQ7xT2pLmZaVq1sB0dA",
  "message": "PDF 已生成",
  "request_id": "b7d1f0c2a1e34f8f9a1c2d3e4f5a6b7c",
  "page_count": 1,
  "duration_ms": 812
}
```

响应头含 `Deprecation: true`、`Link: </v1/render>; rel="successor-version"`。旧路径的 `file_url` 与 `v1` 下载端点一致，**不再**暴露 `/files/*` 静态目录（旧静态挂载移除，避免越权遍历与无 TTL 的历史文件）。

## 5. 统一错误码表

错误响应统一结构（`response_mode=binary` 时也是 JSON）：

```json
{
  "success": false,
  "code": "INVALID_ARGUMENT",
  "message": "page.margin 超出允许范围",
  "request_id": "b7d1f0c2a1e34f8f9a1c2d3e4f5a6b7c",
  "details": [{"field": "page.margin.top", "reason": "must be <= 50"}]
}
```

| `code` | HTTP | 触发场景 | 调用方建议 |
| --- | --- | --- | --- |
| `UNAUTHORIZED` | 401 | 缺少或错误的 API Key | 修正密钥，勿重试 |
| `INVALID_ARGUMENT` | 400 | 字段校验失败、`html` 为空、页面尺寸/边距/超时越界 | 按 `details` 修正参数 |
| `INVALID_JSON` | 400 | 请求体不是合法 JSON | 修正序列化 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | `Content-Type` 非 JSON/表单 | 改 Content-Type |
| `PAYLOAD_TOO_LARGE` | 413 | 请求体或 HTML 超过上限 | 精简 HTML（内联资源建议改用基址 + 更省体积编码） |
| `RATE_LIMITED` | 429 | 超过 QPS 配额或队列已满（`QUEUE_FULL_STRATEGY=429`） | 按 `Retry-After` 退避 |
| `SERVICE_BUSY` | 503 | 队列已满（`QUEUE_FULL_STRATEGY=503`）或引擎重建中 | 按 `Retry-After` 退避 |
| `RENDER_TIMEOUT` | 504 | 渲染超过 `timeout_ms` | 增大 `timeout_ms`（上限内）或精简 HTML |
| `RENDER_FAILED` | 500 | 渲染引擎报错（HTML 本身导致） | 检查 HTML（如超大图片、非法字体声明）；可重试一次 |
| `PDF_TOO_MANY_PAGES` | 422 | 输出页数超 `max_pages` | 精简内容或提高 `max_pages` |
| `RENDER_OUTPUT_TOO_LARGE` | 422 | 输出 PDF 超 `MAX_PDF_BYTES` | 精简内联资源 |
| `NOT_FOUND` | 404 | 请求路径不存在（路由未匹配） | 检查路径拼写 |
| `FILE_NOT_FOUND` | 404 | 产物不存在（或已被清理） | 重新渲染 |
| `FILE_EXPIRED` | 410 | 产物超过 TTL | 重新渲染 |
| `ENGINE_UNAVAILABLE` | 503 | 渲染引擎不可用（重建失败/依赖缺失） | 退避重试；持续则告警 |
| `INTERNAL_ERROR` | 500 | 未预期异常（已记录堆栈，不外泄） | 带 `request_id` 报障 |

约定：

- 错误码是**封闭集合**，新增必须同步文档与测试；
- 服务端绝不在响应中回显堆栈、文件路径、库版本、HTML 片段；
- `429`/`503` 必带 `Retry-After`（秒）。

## 6. 限流与调用限制

| 限制 | 默认值 | 配置项 |
| --- | --- | --- |
| 请求频率 | 60 次/分钟，突发 10（令牌桶，按 API Key 维度；未鉴权时按客户端 IP） | `RATE_LIMIT_RPM`、`RATE_LIMIT_BURST` |
| 并发渲染 | 4 | `MAX_CONCURRENT_RENDERS` |
| 排队深度 | 8（超出按 `QUEUE_FULL_STRATEGY` 快速失败） | `MAX_QUEUE_SIZE` |
| 请求体 | 4 MiB | `MAX_REQUEST_BYTES` |
| HTML | 2 MiB（硬上限 10 MiB） | `MAX_HTML_BYTES` |
| 渲染超时 | 20s（上限 30s） | `RENDER_TIMEOUT_MS`、`RENDER_MAX_TIMEOUT_MS` |
| 输出页数 | 5 页（简历目标为一页，留出少量溢出余量） | `MAX_PAGES` |
| 输出体积 | 20 MiB | `MAX_PDF_BYTES` |
| 产物 TTL | 3600s（上限 7 天） | `ARTIFACT_TTL_SECONDS` |

超过频率限制的请求**不进入渲染**，因此不会占用并发闸门（快速失败优先）。排队等待计入总超时预算：`queue_wait + render_time <= timeout_ms`。

## 7. 幂等与重试语义

- 渲染本身是确定性纯函数：相同 `html` + 相同参数 → 稳定输出（同引擎版本下字节级稳定）。
- 服务端**不支持** `Idempotency-Key`：每次成功调用都会生成新的 `file_id` 与新产物，重复提交会产生多份文件，由调用方按 `file_name` 自行去重。
- 可安全重试的错误：`SERVICE_BUSY`、`ENGINE_UNAVAILABLE`、`RATE_LIMITED`、`RENDER_TIMEOUT`、`INTERNAL_ERROR`。
- 不建议重试：`INVALID_ARGUMENT`、`PAYLOAD_TOO_LARGE`、`PDF_TOO_MANY_PAGES`、`RENDER_OUTPUT_TOO_LARGE`（确定性失败）。

## 8. 星辰智能体平台接入

平台按 OpenAPI Schema 导入并据此生成工具调用，接入点为**服务自身提供的精简契约**：

```text
GET https://<你的域名>/v1/openapi.agent.yaml
```

该文档只包含平台需要的两个操作（`POST /v1/render`、`GET /v1/files/{file_id}`），并且：

- `servers[0].url` 在运行时按 `PUBLIC_BASE_URL` 注入，域名分配后只需改环境变量并重启，无需改 Schema 文件；
- 鉴权方式为 `X-API-Key`（`ApiKeyAuth`），在平台侧配置为固定请求头；
- 请求参数全部为基本类型与确定的嵌套对象，不使用联合类型，避免平台表单生成失败；
- 描述文本面向模型：`html` 的说明会明确写出「原样渲染、不得依赖外链资源、须内联 CSS 与 data URI 资源」。

完整契约（含运维端点与旧接口）仍可通过 `GET /v1/openapi.yaml` 获取，用内部工具链与网关配置，不建议导入平台（多余操作会干扰模型选择）。

平台侧建议配置：把 `file_name` 的默认值设为 `resume.pdf`；`response_mode` 保持默认 `url`，便于在对话中直接给出下载链接；把 `timeout_ms` 从模型可见参数中去掉或固定为默认值，降低误用概率。

## 9. 版本与演进

- 路径版本化：`/v1/`。破坏性变更进 `/v2/`，`/v1/` 至少保留一个发布周期。
- 新增可选字段为兼容变更，不改主版本。
- 旧 `POST /render` 与 `/files/*` 静态目录的移除计划在 v2 提出，需你确认后执行（既有调用方是否已上线见决策点）。
