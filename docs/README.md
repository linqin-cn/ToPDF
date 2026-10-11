# HTML 转 PDF 服务开发文档

本目录是本次交付的全部设计与计划文档。编码前请先确认 `00` 号文档中的决策点。

| 文档 | 内容 |
| --- | --- |
| [00-recon-and-decisions.md](00-recon-and-decisions.md) | 仓库现状勘察、技术选型建议、待确认决策点 |
| [01-requirements.md](01-requirements.md) | 需求边界与明确不做的范围 |
| [02-architecture.md](02-architecture.md) | 架构设计、目录结构、渲染引擎与并发模型、安全模型 |
| [03-api-contract.md](03-api-contract.md) | 接口契约、参数表、错误码表、限流与重试语义 |
| [04-openapi.yaml](04-openapi.yaml) | OpenAPI 3.0.3 Schema（权威契约） |
| [05-deployment-ops.md](05-deployment-ops.md) | 配置项、镜像构建、运行时加固、监控告警、运维手册 |
| [06-test-and-acceptance.md](06-test-and-acceptance.md) | 测试分层、用例清单、保真评估方法、验收标准 |
| [07-task-plan.md](07-task-plan.md) | 分阶段任务清单、里程碑、需你配合的事项 |

辅助工具：`scripts/validate_openapi.py` 校验 OpenAPI 契约的结构与 `$ref` 完整性。

```bash
python scripts/validate_openapi.py
```

实施期还会生成 `08-test-report.md` 与 `09-acceptance-report.md`（真实执行输出与逐条验收证据）。
