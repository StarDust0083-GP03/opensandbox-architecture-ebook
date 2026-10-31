# 附录 B：来源、版本与事实边界

## 上游快照

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/opensandbox-group/OpenSandbox` |
| 提交 | `c2f55b20e945d89efe3d35220942a997a3551751` |
| 提交时间 | 2026-10-30 23:09:43 +0800 |
| 提交标题 | `Merge pull request #2495 from opensandbox-group/osep-0025-w1-contracts-and-resolver` |
| 分支 | `main` |
| 核对日期 | 2026-10-31 |
| 克隆方式 | `git clone --depth 1`（浅克隆，不含历史） |
| 许可 | 代码 Apache-2.0；文档 CC BY 4.0（CHARTER 第 7.b.iv 条） |

书稿引用的所有路径、行数、字段名、常量都对应这个提交。上游继续演进后，个别细节会漂移；遇到不一致时以该提交为准，或用你手上的版本重新核对。

## 阅读方法

**完整读过**（作为章节事实来源）：

- `README.md`、`CHARTER.md`、`ROADMAP.md`
- `docs/architecture/index.md`、`control-plane/server.md`、`control-plane/operator.md`、`data-plane/execd.md`、`data-plane/node-agent.md`
- `docs/architecture/network/ingress.md`、`egress.md`、`single-host-network.md`
- `docs/architecture/fast-sandbox/` 下 `index`、`templates`、`scheduling`、`networking`、`checkpoints`、`ha`、`storage`、`firecracker`、`performance`
- `docs/api/index.md`、`docs/api/implementation-compatibility.md`
- `docs/getting-started/installation.md`、`configuration.md`
- `docs/guides/` 中的 `pause-resume`、`credential-vault`、`multi-tenancy`、`lifecycle-hooks`、`isolated-sessions`（含关键小节）、`client-pool`（前半）、`secure-container`（概览与兼容矩阵）、`telemetry`（章节结构）
- `docs/deployment/index.md`（前 180 行）
- `specs/` 四份契约的头部与 `info` 段、`specs/README.md`
- `oseps/` 目录清单（24 份）与 `oseps/README.md` 未逐篇通读
- `CONTRIBUTING.md` 的 License 节、`LICENSE` 头部

**按需查阅**：`docs/guides/` 其余指南只看标题结构与关键段落（`renew-on-access`、`auto-pause-wake`、`network-isolation`、`egress-upstream-proxy`、`secure-access`、`windows-sandbox` 等），用于交叉验证具体结论。

**源码**：逐文件读过或片段引用 `server/`（`main.py`、`startup_guard.py`、`api/lifecycle.py`、`api/schema.py` 部分、`services/sandbox_service.py`、`factory.py`、`composite_service.py`、`runtime_resolver.py`、`services/docker/networking.py` 片段）、`components/execd`（`web/controller/command.go`、`web/model/codeinterpreting.go`）、`components/egress`（`pkg/policy/policy.go`、`always_rules.go`）、`components/ingress/pkg/proxy/header.go`、`kubernetes/apis/sandbox/*/batchsandbox_types.go`、`sdks/sandbox/python/.../sandbox.py` 片段、`sdks/sandbox/go/go.mod`、若干 `Chart.yaml`/`values.yaml`。其余代码只做了目录级与 grep 级观察。

## 没有做过的事

这一节比上面那节重要。

- **没有部署或运行 OpenSandbox。** 没有起服务器、没有建沙箱、没有跑它的 e2e 测试。
- **没有做性能测量。** 书里出现的每个毫秒数都是上游文档的数字，附了出处与适用条件。
- **没有做安全审计。** 对 nftables 规则、TLS 拦截、路径守卫的描述是「文档说它这样实现」，不是「我验证过它没有漏洞」。
- **没有做许可的合规审查。** 只记录了代码 Apache-2.0、文档 CC BY 4.0 这一事实。把上游材料用于商业用途前应自行确认。
- **没有通读 24 份 OSEP。** 引用 OSEP 编号处，依据的是文档与 ROADMAP 中对它的转述。
- **没有验证 README 里的所有集成示例**（30+），也没有判断它们的可运行性。

## 数字的出处

| 数字 | 出处 | 适用条件 |
|---|---|---|
| 创建 p50 64.0 ms / p90 93.8 ms / p99 108.3 ms | `docs/architecture/fast-sandbox/performance.md` | ACK 两节点，2026-10-08，模板已缓存，串行 100 次，SDK 就绪口径 |
| 并发 10：p50 102.7 ms / p99 125.4 ms | 同上 | 100/100 成功 |
| 并发 20：p50 136.7 ms，51/100 成功 | 同上 | **只含成功尝试**；48 次 429 + 1 次就绪超时被排除 |
| 运行时创建 p50 33.5–50.0 ms | 同上 | runtime 日志，止于运行时创建，不含 SDK 就绪 |
| SDK 同节点恢复中位 62.66 s | 同上 | 3 个样本；运行时恢复 35.49 ms；差距未定位 |
| 100 沙箱 0.92 s（BatchSandbox 池化） | `docs/architecture/control-plane/operator.md` | 与 agent-sandbox 76.4/23.2/33.9 s 对比；不同实现路径 |
| 隔离会话创建约 100 ms | `docs/guides/isolation-sessions.md` | bwrap 启动 + 短暂等待 |
| egress 9 秒停止预算（Docker） | `docs/architecture/network/egress.md` | 删除时 |
| ingress WebSocket 握手预算 45 s、消息 64 MiB | `docs/architecture/network/ingress.md` | — |
| 单个事件行最大 16 MiB | `docs/architecture/data-plane/execd.md` | 会话输出切分 |
| 后台命令输出保留 24 h | 同上 | 已完成的后台命令 |

所有性能数字都来自上游自测，本书没有复现。它们的可比性受环境、版本、口径影响，不能直接当作你的部署预期。

## 观察到的文档与命名不一致

下面几处是我在阅读中确认存在的漂移，写下来供读者避免困惑（都是事实观察，不是缺陷主张）：

1. **Go module 路径与 npm scope 仍用 `alibaba`。** `sdks/sandbox/go/go.mod` 是 `module github.com/alibaba/OpenSandbox/sdks/sandbox/go`；JS 包名是 `@alibaba-group/opensandbox`；JVM 坐标是 `com.alibaba.opensandbox`。仓库已迁到 `opensandbox-group` 组织下，但发布坐标没有跟着改。README 同时列出了新旧两种形态。
2. **`/pty` 路由不在 `execd-api.yaml` 里。** 实现已存在，契约未覆盖；文档明确说不能从 OpenAPI 兼容推出「支持这类扩展」。
3. **隔离会话的 `diff`/`commit` 在契约里但未实现**，返回 `503`，`/v1/isolated/capabilities` 如实报告。
4. **`server/v*` 冻结标签不再标识较新的服务器发布**，统一发布标签是 `release-X.Y.Z`；文档专门提醒不要用旧标签判断版本。
5. **文档目录与指南术语小差异**：`docs/guides/` 有 `auto-pause-wake.md` 与 `auto-pause-wake-on-access.md` 两个相邻主题（前者是操作指南，后者是架构说明），阅读时容易混。本书第 13 篇把两者合并在「访问驱动的两个方向」一节。
6. **`specs/diagnostic-api.yml` 用 `.yml` 后缀，另外三份用 `.yaml`。** 这个不一致会绊到批量脚本。

## OSEP 索引（24 份）

`oseps/` 是距离「设计意图」最近的证据。本书引用过的几份：

| 编号 | 主题 | 本书位置 |
|---|---|---|
| 0004 | 安全容器运行时 | 第 15 篇 |
| 0005 | 客户端沙箱池 | 第 13 篇 |
| 0007 | Fast Sandbox 运行时支持 | 第 8 篇 |
| 0008 | 暂停/恢复 via rootfs 快照 | 第 13 篇 |
| 0009 | 访问即续期 | 第 10、13 篇 |
| 0011 | 安全 endpoint 访问 | 第 10 篇 |
| 0012 | Credential Vault | 第 12 篇 |
| 0013 | 隔离执行 API | 第 9 篇 |
| 0014 | 多租户 | 第 14 篇 |
| 0018 | execd 作为 init | 第 9 篇 |
| 0019 | 节点级沙箱采集 | 第 16 篇 |
| 0022 | 多沙箱 egress 控制面 | 第 8、11、12 篇 |
| 0023 | 凭据绑定的 TLS 拦截 | 第 11 篇 |
| 0024 | Fast Sandbox 空闲暂停与唤醒 | 第 13 篇 |

其余编号覆盖：FQDN 出向控制（0001）、agent-sandbox 支持（0002）、卷与卷绑定（0003）、开发者控制台（0006）、OpenTelemetry（0010）、Pod 快照（0015）、统一 umbrella 发布治理（0016）、SDK 传输韧性（0017）、生命周期钩子（0020）、可扩展的客户端池预热（0021）。提交标题里出现的 0025 在本快照的 `oseps/` 目录里还没有对应文件。

## 本书与上游的关系

- 本书是**独立撰写**的中文解读，不是上游文档的翻译，也不是其官方出版物。
- 引用上游文档与代码时保持简短并标注路径；结构性结论按「源码观察 / 文档陈述 / 教学推演 / 教学场景」区分（见前言）。
- 书中的教学实现 `examples/mini_osb.py` 是原创的，用标准库重写本书讨论的结构，不与上游代码共享来源。
- 书稿许可建议沿用上游文档的 CC BY 4.0 精神（署名 + 相同方式说明来源），但这是作者的选择，不是上游对本项目的授权。上游代码与文档的许可各自独立。
- 「OpenSandbox」是上游项目的名称，本书不主张任何商标权利，也不代表上游立场。
