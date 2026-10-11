# 部署与运维方案

## 1. 配置管理

全部配置经环境变量注入，前缀 `RPR_`（`OUTPUT_DIR`、`PUBLIC_BASE_URL` 保留为兼容别名）。启动时由 `pydantic-settings` 全量校验，非法值直接启动失败。`deploy/.env.example` 提供完整样例。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RPR_HOST` / `RPR_PORT` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `RPR_WORKERS` | `1` | 保持 1：并发闸门与浏览器实例为进程内资源，多 worker 会使实际并发成倍放大；需要更高吞吐应水平扩展副本 |
| `RPR_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `RPR_LOG_FORMAT` | `json` | `json`（生产）/ `console`（本地） |
| `AUTH_ENABLED` | `true` | 关闭后忽略鉴权，仅限纯内网 |
| `API_KEYS` | 空 | 逗号分隔密钥列表（常量时间比较）；`AUTH_ENABLED=true` 且为空则启动失败 |
| `RATE_LIMIT_RPM` / `RATE_LIMIT_BURST` | `60` / `10` | 令牌桶频率与突发 |
| `MAX_CONCURRENT_RENDERS` | `4` | 并发渲染闸门 |
| `MAX_QUEUE_SIZE` | `8` | 排队深度 |
| `QUEUE_FULL_STRATEGY` | `429` | 队列满时快速失败策略：`429 RATE_LIMITED` 或 `503 SERVICE_BUSY` |
| `MAX_REQUEST_BYTES` | `4194304` | 请求体上限（4 MiB） |
| `MAX_HTML_BYTES` | `2097152` | HTML 上限（2 MiB），不得超过 `HARD_MAX_HTML_BYTES` |
| `HARD_MAX_HTML_BYTES` | `10485760` | 硬上限（10 MiB） |
| `RENDER_ENGINE` | `chromium` | `chromium` 或 `weasyprint` |
| `RENDER_TIMEOUT_MS` / `RENDER_MAX_TIMEOUT_MS` | `20000` / `30000` | 默认与最大渲染超时 |
| `BROWSER_RECYCLE_AFTER` | `200` | 浏览器实例在若干次渲染后主动回收 |
| `RENDER_TMP_DIR` | `/tmp/rpr` | 请求级临时目录根 |
| `DEFAULT_PAGE_SIZE` | `A4` | HTML 无 `@page` 时的回退尺寸 |
| `MAX_PAGES` / `HARD_MAX_PAGES` | `5` / `100` | 输出页数上限（简历目标一页，默认留少量溢出余量） |
| `MAX_PDF_BYTES` | `20971520` | 输出 PDF 上限（20 MiB） |
| `ARTIFACT_DIR` | `/data/artifacts` | 产物目录 |
| `ARTIFACT_TTL_SECONDS` / `HARD_MAX_TTL_SECONDS` | `3600` / `604800` | 产物有效期与硬上限 |
| `CLEANUP_INTERVAL_SECONDS` | `300` | 清理任务周期 |
| `CLEANUP_LEADER_ONLY` | `false` | 多副本共用卷时置 true，仅一副本执行清理 |
| `PUBLIC_BASE_URL` | 空 | 对外基址；为空时按请求 `Host` 推导（仅限可信反代之后） |
| `DOWNLOAD_URL_MODE` | `key` | `key`（需 API Key）或 `signed`（HMAC 短期签名链接） |
| `SIGNING_SECRET` | 空 | `signed` 模式必填，至少 32 字符 |
| `RESPONSE_MODE` | `url` | 默认响应形态，可被请求参数与 `Accept` 覆盖 |
| `METRICS_ENABLED` | `true` | 关闭后 `/metrics` 返回 404 |
| `METRICS_TOKEN` | 空 | 非空时 `/metrics` 需 `Authorization: Bearer <token>` |
| `TRACING_ENABLED` | `false` | OpenTelemetry 开关 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | OTLP 端点 |
| `OTEL_SERVICE_NAME` | `resume-pdf-renderer` | 服务名 |
| `LOG_SAMPLE_RATE` | `1.0` | 访问日志采样率，高流量时可下调 |

## 2. 镜像与构建

多阶段构建，运行期非 root：

1. builder：`python:3.13-slim-bookworm` + `uv`，`uv sync --frozen` 生成虚拟环境。
2. runtime：`python:3.13-slim-bookworm`，安装 `playwright install --with-deps chromium` 所需系统库与 `fonts-noto-cjk`，拷贝 builder 的虚拟环境与源码。
3. 创建非 root 用户 `rpr`（固定 uid，便于卷权限），仅暴露 `8000`，`HEALTHCHECK` 打 `/readyz`。
4. 构建期固定 `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`，并把 `playwright` 版本锁进 `uv.lock`，保证浏览器与驱动同版本。

备选方案是直接基于 `mcr.microsoft.com/playwright/python:<version>-noble`，省去系统库安装，代价是基础镜像不可控；v1 采用上面的方案，把依赖钉在自己手里。

预期镜像体积 1.2–1.6 GB（Chromium + 依赖 + CJK 字体）。若体积敏感，可切 `RENDER_ENGINE=weasyprint` 省掉 Chromium（体积约 400 MB），代价是保真度下降，详见 `02-architecture.md` 第 2.1 节。

## 3. 运行时加固

```yaml
# deploy/docker-compose.yml 摘要
services:
  renderer:
    image: resume-pdf-renderer:1.0.0
    read_only: true
    user: "10001:10001"
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    pids_limit: 512
    mem_limit: 2g
    cpus: 2.0
    tmpfs:
      - /tmp:size=512m,mode=1777
    volumes:
      - artifacts:/data/artifacts
    environment:
      RPR_PUBLIC_BASE_URL: https://pdf.example.com
      RPR_API_KEYS: ${RPR_API_KEYS:?required}
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/readyz"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

要点：

- 根文件系统只读，唯一可写位置是 `tmpfs /tmp` 与产物卷，天然满足「临时文件受控」。
- Chromium 沙箱取舍的显式说明：容器内以非 root 运行并设 `chromium_sandbox=False`（等价 `--no-sandbox`）。原因是容器本身已是隔离边界——无网络、只读根、能力集清空、PID 与内存受限、`no-new-privileges`——而 Chromium 自带沙箱在非 root、无 `CAP_SYS_ADMIN` 的容器里无法启用。若合规要求必须叠加浏览器层沙箱，替代方案是给容器加 `--cap-add SYS_ADMIN`，或改用 gVisor/Kata 运行时；这会扩大容器攻击面，需要你确认后我再实现（见 `00-recon-and-decisions.md` 决策点）。
- 反向代理（Nginx/Traefik）需透传 `X-Request-Id` 与 `X-API-Key`，并设置 `client_max_body_size 8m`、`proxy_read_timeout 60s`。生产建议只暴露 HTTPS，`/metrics` 与 `/docs` 仅内网可达。
- 契约端点：`/v1/openapi.yaml`（完整）与 `/v1/openapi.agent.yaml`（星辰平台导入用，仅渲染与下载两个操作）。两者的 `servers[0].url` 都按 `PUBLIC_BASE_URL` 运行时注入，因此域名分配后只需设置该环境变量并重启，无需修改或重新分发 Schema 文件。`PUBLIC_BASE_URL` 为空时回退为按请求 `Host` 推导，仅适用于可信反代之后的场景。

## 4. 健康检查与探针

| 端点 | 用途 | 判据 |
| --- | --- | --- |
| `GET /healthz` | 存活探针（liveness） | 进程可响应即 200，不检查依赖，避免依赖抖动触发重启风暴 |
| `GET /readyz` | 就绪探针（readiness） | 渲染引擎可用（浏览器存活或可重建）且 `ARTIFACT_DIR` 可写 |

K8s 建议：`initialDelaySeconds: 15`（浏览器冷启动），`failureThreshold: 3`，`terminationGracePeriodSeconds: 30`；服务收到 `SIGTERM` 后停止接单、等待在飞渲染结束（上限 25s），关闭浏览器再退出。

## 5. 日志

- 格式：单行 JSON，字段包括 `ts`、`level`、`logger`、`event`、`request_id`、`trace_id`、`method`、`path`、`status`、`duration_ms`、`queue_wait_ms`、`render_ms`、`html_bytes`、`html_sha256_12`、`page_count`、`pdf_bytes`、`engine`、`error_code`、`client_key`。
- 分级：`DEBUG` 细节（如路由拦截统计）；`INFO` 每请求一条访问日志与生命周期事件；`WARNING` 可恢复问题（引擎重建、清理失败、限流命中）；`ERROR` 渲染失败与未捕获异常（附堆栈，仅落日志）。
- 隐私：永不记录 HTML 正文、PDF 内容与 API Key 明文（Key 仅记末 4 位指纹）。
- 采样：`ERROR`/`WARNING` 全量，访问日志按 `LOG_SAMPLE_RATE` 采样。

## 6. 指标（Prometheus）

| 指标 | 类型 | 标签 | 用途 |
| --- | --- | --- | --- |
| `rpr_render_requests_total` | counter | `engine`、`response_mode`、`result`、`error_code` | 请求量与失败分类 |
| `rpr_render_duration_seconds` | histogram | `engine`、`media` | 端到端时延（P50/P95/P99） |
| `rpr_render_queue_wait_seconds` | histogram | 无 | 排队等待，用于区分容量问题与渲染问题 |
| `rpr_render_inflight` | gauge | 无 | 在飞渲染数，与 `MAX_CONCURRENT_RENDERS` 对比 |
| `rpr_render_queue_depth` | gauge | 无 | 排队深度 |
| `rpr_queue_rejections_total` | counter | `strategy` | 队列满导致的拒绝 |
| `rpr_rate_limit_rejections_total` | counter | `limit` | 限流拒绝 |
| `rpr_html_bytes` / `rpr_pdf_bytes` / `rpr_render_pages` | histogram | 无 | 输入输出规模分布 |
| `rpr_engine_ready` | gauge | 无 | 1 就绪，0 不可用 |
| `rpr_engine_restarts_total` | counter | `cause` | 引擎重建（崩溃/超时/主动回收） |
| `rpr_artifacts_created_total` / `rpr_artifacts_deleted_total` | counter | 无 | 产物生命周期 |
| `rpr_cleanup_errors_total` | counter | `reason` | 清理异常 |

进程与 GC 指标沿用 `prometheus_client` 默认的 `process_*`、`python_gc_*`。

建议告警规则：

1. `rpr_engine_ready == 0` 持续 1 分钟。
2. 5 分钟错误率超过 5%。
3. `histogram_quantile(0.95, rpr_render_duration_seconds) > 5`。
4. `rpr_queue_rejections_total` 的 5 分钟增量大于 0。
5. 容器内存使用率超过 85%。

## 7. 追踪

`TRACING_ENABLED=true` 时启用 OpenTelemetry（OTLP/HTTP 导出），span 结构：

```text
http.server POST /v1/render          attrs: http.status_code, rpr.html_bytes, rpr.response_mode
 |- rpr.render.process               attrs: rpr.engine, rpr.media, rpr.page_count, rpr.pdf_bytes
 |   |- rpr.render.goto             route-intercept 装载 HTML
 |   |- rpr.render.printToPDF
 |- rpr.storage.put
```

`request_id` 与 `trace_id` 关联：启用追踪时 `request_id` 复用 `trace_id`，未启用时使用 UUID4。默认关闭以降低依赖与开销。

## 8. 容量与扩容

- 单副本基线（4 vCPU / 4 GB，`MAX_CONCURRENT_RENDERS=4`）：单页简历渲染约 0.4–1.2s，整机吞吐约 3–8 req/s。
- 水平扩展：副本间无状态，产物目录需换成共享卷或对象存储（`ArtifactStore` 已是端口，替换实现即可）；若用共享卷，把 `CLEANUP_LEADER_ONLY` 置为 true，只让一个副本执行清理。
- 扩容信号：`rpr_render_inflight` 持续贴近上限、`rpr_render_queue_wait_seconds` P95 超过 1s、队列拒绝率上升。
- 缩容：单请求最长 30s 渲染加 25s 优雅退出，K8s 需保证 `terminationGracePeriodSeconds` 足够。

## 9. 运维手册（常见故障）

| 现象 | 排查 | 处置 |
| --- | --- | --- |
| `/readyz` 503 且 `engine=error` | 查看 `rpr_engine_restarts_total{cause}` 与日志事件 `engine_restart_failed` | 重启容器；持续失败则检查 Chromium 依赖与 `/dev/shm` 大小 |
| 大量 `RENDER_TIMEOUT` | 查看 `rpr_render_duration_seconds` 与 `rpr_html_bytes` 分布 | 定位超大 HTML；必要时上调 `RENDER_MAX_TIMEOUT_MS` 与容器 CPU |
| 内存持续增长 | 查看容器 RSS 与 `rpr_engine_restarts_total{cause="recycle"}` | 下调 `BROWSER_RECYCLE_AFTER` |
| 磁盘写满 | `du -sh $ARTIFACT_DIR`，确认 `rpr_artifacts_deleted_total` 是否仍在增长 | 缩短 `ARTIFACT_TTL_SECONDS`，检查清理任务是否被 leader 逻辑阻塞 |
| 中文显示为方框 | 容器内 `fc-list | grep -i noto` 是否有输出 | 确认 `fonts-noto-cjk` 已安装；自定义字体经挂载卷提供 |
| 下载 404 或 410 | 产物是否超过 TTL、是否已被清理 | 属预期行为，重新渲染；需要长效链接则改用对象存储 |
| 返回 5xx 但无渲染日志 | 中间件日志是否缺失 | 若在鉴权或限流阶段失败，这类失败不产生渲染日志 |

## 10. 升级与回滚

- 镜像标签使用不可变版本号（如 `1.0.0`）而非 `latest`；`uv.lock` 保证依赖可复现。
- 发布顺序：新副本通过 `/readyz` 后切流，再排空旧副本。
- 回滚：切回上一版本镜像；配置向后兼容（新增项都有默认值）。
- 破坏性契约变更只走 `/v2/`，`/v1/` 与旧 `/render` 在同一大版本内保持兼容。
