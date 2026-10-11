# HTML 转 PDF 服务：现状勘察与选型决策

> 本文记录交付前的仓库勘察结论、技术选型建议，以及必须由你确认的决策点。
> 确认方式：直接在对话中回复「按建议执行」或对具体编号给出修改意见。

## 1. 仓库勘察结论（截至本次勘察）

| 项目 | 现状 |
| --- | --- |
| 仓库 | `https://github.com/linqin-cn/ToPDF.git`，分支 `master`，远端与本地产分支一致 |
| 提交历史 | 仅 1 次提交 `d592649 初始化仓库`，工作区干净 |
| 提交者 | `linqin-cn <yilf4399@gmail.com>`（本地 git config，无 GPG 签名） |
| 语言 | Python 3.13.9（本机唯一可用解释器，位于 `D:\Python`） |
| Web 框架 | FastAPI 0.115.6 + Uvicorn 0.34.0（`[standard]`，含 uvloop 之外的 httptools/websockets） |
| 渲染引擎 | WeasyPrint 63.1（纯 Python，依赖系统 Pango/Cairo/Harfbuzz） |
| 依赖管理 | `requirements.txt`，全部 `==` 精确锁版本，无 `pyproject.toml`、无 lockfile |
| 构建工具 | 仅 `Dockerfile`（`python:3.11-slim-bookworm`，apt 安装 Pango/Cairo + `fonts-noto-cjk`） |
| 目录结构 | 扁平：根目录单文件 `app.py`（85 行），无包结构、无分层 |
| 已有配置 | 仅两个环境变量：`OUTPUT_DIR`、`PUBLIC_BASE_URL` |
| CI/CD | 无（无 `.github/`、无任何流水线配置） |
| 测试 | 无测试目录、无测试依赖（`httpx` 未安装，`TestClient` 亦不可用） |
| 代码规范 | 无 linter/formatter 配置（无 ruff/black/mypy/pre-commit） |
| 版本控制卫生 | 根目录**无** `.gitignore`，`.venv/`、`.idea/workspace.xml` 靠子目录自带 `.gitignore` 才未被跟踪；`.idea/` 其余文件已被跟踪 |
| 现有接口 | `GET /health`、`POST /render`（JSON 或表单二选一，返回 `file_url`）、`GET /files/{name}` 静态文件 |
| 现有缺陷 | ①同步阻塞渲染直接在事件循环中执行，会卡死并发 ②无大小/超时/并发限制 ③异常全量回显给调用方 ④`base_url` 指向应用目录，HTML 可读取本地文件系统 ⑤无鉴权、无日志、无清理策略 ⑥错误响应结构（`success/message`）与 HTTP 语义不统一 |

### 本地环境约束（影响验证方式）

- **WeasyPrint 在本机 Windows 上无法运行**：`libgobject-2.0-0` 等 GTK 动态库缺失，导入即 `OSError`。
- **Docker Desktop 29.6.1 可用**（沙箱内访问 npipe 需授权，已确认守护进程正常）。
- **WSL 不可用**（`Wsl/EnumerateDistros` 返回 `E_ACCESSDENIED`）。
- 结论：**以 Linux 容器作为渲染能力的唯一权威验证环境**；本机 `.venv` 只用于跑纯 Python 单元测试与静态检查。

## 2. 技术选型建议

主技术栈（Python + FastAPI + Pydantic + Docker）由现有代码决定，**建议保持不变**，理由是回归成本最低、现有工作流（星辰）已按 `POST /render` 对接。
唯一需要真正决策的是**渲染引擎**，它决定了「高保真」这一核心目标的达成上限。

### 2.1 渲染引擎对比

| 维度 | A. WeasyPrint 63（现状） | B. Chromium Headless（Playwright，建议） |
| --- | --- | --- |
| CSS 保真度 | 印刷排版 CSS 强；Flex 基本可用，Grid/现代特性支持不完整，越界样式会静默降级 | 与主流浏览器一致，是事实上的 HTML 渲染基准 |
| 对「严禁改动 HTML」的适配 | 引擎不支持的写法**无法由其自身修正**，且我们被禁止补 CSS，只能接受降级结果 | 智能体按浏览器语义写的 HTML 能得到预期结果 |
| JS | 不执行（天然安全） | 可执行 → **以 `java_script_enabled=False` 关闭**，保留 CSS 能力、去掉脚本面 |
| 资源占用 | 轻，镜像约 300–400MB，单进程内存低 | 重，镜像约 1.2–1.6GB，每渲染上下文数十 MB |
| 资源隔离 | 与 API 同进程，需自行做进程级隔离 | 浏览器本身多进程，配合容器限额隔离性更好 |
| 远程资源拦截 | 自定义 `url_fetcher` 可做到「零网络」 | `context.route()` 全量阻断，同样可做到「零网络」 |
| 冷启动 | 快（毫秒级） | 浏览器启动数百毫秒，需常驻复用 |
| 中文字体 | 依赖系统字体，需装 `fonts-noto-cjk` | 同左 |
| 运维复杂度 | 低 | 中（需常驻浏览器实例池、僵尸进程回收） |

**建议：选 B（Chromium Headless）+ 关闭 JS + 全量阻断网络**，理由是核心诉求是「高保真且不得由我们补样式」，这正是 Chromium 相对 WeasyPrint 的确定性优势；其成本（镜像体积、常驻实例）可通过多阶段镜像与实例池控制，且对调用方无感。
同时保留 `RenderEngine` 端口抽象，使 WeasyPrint 作为降级实现可插拔，避免把选型写死。

### 2.2 运行形态建议

建议 v1 采用**单容器双角色**：同一 FastAPI 进程内维护受控的浏览器实例池，渲染一律走独立线程/进程边界 + 硬超时，容器层施加 `--memory/--cpus/--pids-limit`。
不建议 v1 就拆成 API + Renderer 两个网络服务：会引入队列、重试、一致性等额外复杂度，收益只在超大流量下才显现；架构上预留 `RenderEngine` 端口，后续拆分不改外部契约。

### 2.3 《阿里巴巴开发手册》的适用性说明（重要）

《阿里巴巴 Java 开发手册》面向 Java 生态，本项目为 Python，**无法逐条套用**。建议按以下方式落地其可迁移内核，避免形式主义：

- 命名：《手册》的类/方法/常量命名规则 → 映射为 PEP 8 + Google Python Style（模块/函数 `snake_case`、类 `PascalCase`、常量 `UPPER_SNAKE`、布尔不叫 `is_ok` 之外的模糊名）。
- 常量：《手册》「不允许魔法值」→ 全量提取为 `constants.py` 中的具名常量与枚举。
- 异常：《手册》「不吞异常、不返回 null」→ 统一异常基类 + 全局异常处理器，对外只暴露稳定错误码，内部异常不泄漏堆栈。
- 日志：《手册》日志分级与「禁止用日志代替异常」→ `logging` 结构化输出，区分 `DEBUG/INFO/WARNING/ERROR`，**不记录 HTML 正文**。
- 并发：《手册》线程池命名与资源释放 → 所有并发原语显式命名、统一生命周期管理。
- 集合/空值：Python 侧转化为「不用可变默认参数、显式类型标注、`None` 不参与布尔判断」等等价约束。

若你要求**严格逐条符合《手册》**，唯一路径是把服务改用 Java + Spring Boot 重写，与现有仓库技术栈断裂，成本与风险显著上升——**不建议**，但这是你的决策点。

## 3. 待确认决策点

| 编号 | 决策点 | 建议 | 影响 |
| --- | --- | --- | --- |
| D1 | 渲染引擎 | Chromium Headless（Playwright），关闭 JS | 决定保真上限与镜像体积 |
| D2 | 运行形态 | 单容器双角色，预埋引擎端口 | 决定运维复杂度 |
| D3 | 响应默认形态 | 默认返回 JSON（含 `file_url`，兼容现有工作流），`Accept: application/pdf` 时返回二进制流 | 决定调用方改造量 |
| D4 | 运行时版本 | 统一 Python 3.13（本地已装，Dockerfile 由 3.11 升到 3.13） | 消除本地/容器版本漂移 |
| D5 | 鉴权 | 默认启用 `X-API-Key`，可通过环境变量关闭（内网部署可关） | 决定是否需分配密钥 |
| D6 | 存储 | v1 本地目录 + TTL 清理，预留对象存储适配器 | 决定产物生命周期 |
| D7 | 可观测性 | 结构化日志 + Prometheus 指标为默认；OpenTelemetry 追踪做成开关，v1 默认关闭 | 决定接入工作量 |
| D8 | CI | GitHub Actions 跑 lint + 单测 + 镜像构建与冒烟；仅在推送时执行 | 私有仓库 Actions 有额度限制，需你确认 |
| D9 | 推送权限 | Git push 由我执行还是你执行 | 影响交付节奏 |
| D10 | 中文字体 | 容器内固定 `fonts-noto-cjk`，额外字体由挂载卷提供 | 决定简历字形效果 |

## 4. 待你提供或确认的外部信息

1. **调用方形态**：星辰工作流是最终唯一调用方，还是后续会有多个智能体/多租户？（影响鉴权与限流维度设计）
2. **部署目标环境**：是否有确定的云主机/K8s/内网域名？决定是否需要 `PUBLIC_BASE_URL`、TLS、Ingress 配置。
3. **典型 HTML 规模**：单页简历为主，还是可能出现多页/大表格？（影响默认超时与页数上限取值）
4. **是否需要保留旧 `/render`**：现有工作流是否已上线并被外部依赖？

上述信息缺失不会阻塞 v1：我会按「单租户 + 本地存储 + 兼容旧接口」的假设推进，并在文档中把假设显式标注。

## 5. 决策确认记录（2026-10-11）

你已回复「按建议执行」，并补充了三条关键信息。据此固化的结论如下：

- D1 渲染引擎：Chromium headless（Playwright），渲染期关闭 JS、默认零网络。
- D2 运行形态：单容器单进程，预埋 `RenderEngine` 端口。
- D3 响应形态：默认 `url`（JSON 含 `file_url`），`Accept: application/pdf` 或 `response_mode=binary` 时返回二进制流。
- D4 运行时版本：统一 Python 3.13，本地与镜像一致。
- D5 鉴权：默认启用 `X-API-Key`（亦支持 Bearer），`AUTH_ENABLED=false` 可关闭。
- D6 存储：v1 本地目录加 TTL 清理，`ArtifactStore` 端口预留对象存储。
- D7 可观测性：结构化日志与 Prometheus 默认开启；OpenTelemetry 默认关闭。
- D8 CI：GitHub Actions 门禁，含容器内集成与安全测试。
- D9 推送权限：**由你手动推送**。我只做本地中文提交，不执行 `git push`。
- D10 中文字体：容器固定 `fonts-noto-cjk`，自定义字体经挂载卷提供。
- D11 平台接入：星辰智能体平台通过 **OpenAPI Schema 导入**访问本项目，契约必须满足平台解析器的兼容性要求。
- D12 部署域名：域名已确定但尚未分配，部署时填入 `PUBLIC_BASE_URL`。
- D13 简历页数：内容整体压缩为**一页**，据此设定页数上限与验收口径。

### 5.1 由 D11 派生的契约约束（重要）

星辰平台按 Schema 自动生成工具调用，其解析器通常比标准工具链更保守。为保证一次导入成功，契约按以下规则收敛：

1. 请求参数只用基本类型与确定的嵌套对象，不用联合类型（因此页面尺寸改为「枚举值加独立宽高字段」，边距改为 CSS 风格字符串），避免平台生成错误表单。
2. 每个操作都带 `operationId`、`summary`、`tags`，描述面向「模型如何调用」。
3. 主请求体为 `application/json`；表单与 multipart 仅作兼容保留，不参与平台接入。
4. 不使用跨文件 `$ref`，所有引用都在同一文档内，便于平台整体导入。
5. `servers` 在运行时按 `PUBLIC_BASE_URL` 注入：域名后续分配，平台导入用的 Schema 由服务自身提供（`/v1/openapi.agent.yaml`），无需手改文件。
6. 除完整契约 `docs/04-openapi.yaml` 外，另提供面向平台导入的精简契约（仅渲染与下载两个操作），由脚本从完整契约生成，避免两份文件漂移。

说明：我无法预知星辰解析器的全部实现细节。若首次导入报错，请把平台给出的错误信息发我，我会按其确切限制调整 Schema，而不是先行猜测放宽契约。
