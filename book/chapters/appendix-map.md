# 附录 A：源码阅读地图

这份地图按「读它的目的」组织，不按目录。全部路径相对于上游提交 `c2f55b20e945d89efe3d35220942a997a3551751`。

## 三条阅读路线

**路线一：只想把它跑起来并理解调用方视角（半天）**

```text
README.md
docs/getting-started/index.md
docs/getting-started/configuration.md
docs/api/index.md
sdks/sandbox/python/README.md（或你用的语言）
cli/README.md
```

读完你应该能回答：创建沙箱要哪些参数、endpoint 怎么用、命令输出怎么解析。

**路线二：要评估或自建执行层（两三天）**

```text
docs/architecture/index.md
docs/architecture/control-plane/server.md
docs/architecture/control-plane/operator.md
docs/architecture/data-plane/execd.md
docs/architecture/network/ingress.md
docs/architecture/network/egress.md
docs/api/implementation-compatibility.md
ROADMAP.md
```

再挑一个后端深入：单机看 `single-host-network.md`，集群看 `fast-sandbox/*`。

**路线三：要改代码（一周以上）**

按本书顺序读，每章末尾的「源码入口」表就是路线。下面给一张按角色划分的入口表。

## 按角色划分

### 控制面（Python）

| 路径 | 行数级 | 看什么 |
|---|---|---|
| `server/opensandbox_server/main.py` | ~378 | lifespan、中间件、路由注册、启动断言 |
| `server/opensandbox_server/startup_guard.py` | ~150 | 无 API key 时的确认策略 |
| `server/opensandbox_server/api/lifecycle.py` | ~618 | 创建/查询/删除/暂停路由与校验顺序 |
| `server/opensandbox_server/api/proxy.py` | ~776 | 代理头处理、`Location` 重写、WebSocket |
| `server/opensandbox_server/api/schema.py` | ~1817 | 契约的 Pydantic 落地 |
| `server/opensandbox_server/services/sandbox_service.py` | ~387 | 13 个抽象方法 |
| `server/opensandbox_server/services/factory.py` | ~77 | 配置到实现 |
| `server/opensandbox_server/services/composite_service.py` | ~188 | 前缀分发 |
| `server/opensandbox_server/services/validators.py` | ~723 | 请求侧全部规则 |
| `server/opensandbox_server/services/snapshot_service.py` | ~928 | 公开快照状态机 |
| `server/opensandbox_server/repositories/` | — | SQLite/PostgreSQL 两套适配 |
| `server/opensandbox_server/tenants/` | — | file/http 租户提供方 |
| `server/opensandbox_server/integrations/idle_pause/` | ~11 文件 | 空闲暂停全流程 |
| `server/opensandbox_server/integrations/renew_intent/` | ~9 文件 | 续期意图 |

### Docker 后端

| 路径 | 看什么 |
|---|---|
| `services/docker/docker_service.py` | 生命周期主流程（最大的一块） |
| `services/docker/networking.py` | 网络模式、sidecar 关联、egress 仅 bridge |
| `services/docker/port_allocator.py` | 宿主端口分配 |
| `services/docker/metadata.py` | 保留标签前缀 |
| `services/docker/volumes.py` | 卷归属与 OSSFS |
| `services/docker/snapshot_runtime.py` | commit 本地镜像 |
| `services/docker/windows_profile.py` | Windows 差异 |

### Kubernetes 后端与控制器

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/k8s/kubernetes_service.py` | 服务器侧集成 |
| `server/opensandbox_server/services/k8s/` | provider、endpoint 解析、卷/egress 助手、informer |
| `kubernetes/apis/sandbox/*/batchsandbox_types.go` | `BatchSandboxSpec` |
| `kubernetes/apis/sandbox/*/pool_types.go` | 容量模型字段 |
| `kubernetes/apis/sandbox/*/sandboxsnapshot_types.go` | 快照记录与状态 |
| `kubernetes/internal/controller/batchsandbox_controller.go` | Pod 扩缩、任务、过期、暂停交接 |
| `kubernetes/internal/controller/pool_controller.go` | 缓冲区收敛、分配注解 |
| `kubernetes/internal/controller/poolassign/` | 谓词与打分插件 |
| `kubernetes/internal/controller/sandboxsnapshot_controller.go` | commit Job |

### Fast Sandbox 适配

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/fast_sandbox/service.py` | 创建、暂停/恢复、端点 |
| `docs/architecture/fast-sandbox/index.md` | 外部平台的组件全景 |
| `docs/architecture/fast-sandbox/scheduling.md` | Fastlet 与放置 |
| `docs/architecture/fast-sandbox/networking.md` | 签名 scope 与 subject |
| `docs/architecture/fast-sandbox/checkpoints.md` | 检查点与快照 |

### 数据面（Go）

| 路径 | 看什么 |
|---|---|
| `components/execd/pkg/web/router.go` | 全部路由（含未进契约的 `/pty`） |
| `components/execd/pkg/web/controller/command.go` | 命令执行与 SSE |
| `components/execd/pkg/web/model/codeinterpreting.go` | 事件模型 |
| `components/execd/pkg/web/controller/sse.go` | 流式写入 |
| `components/execd/pkg/web/controller/filesystem_upload.go` | 暂存 + rename |
| `components/execd/pkg/web/controller/pty_ws.go` | PTY 与子协议认证 |
| `components/execd/pkg/isolation/` | bubblewrap 会话 |
| `components/execd/pkg/runtime/` | 加固 launcher、capabilities |
| `components/execd/pkg/jupyter/` | 内核与执行 |
| `components/execd/pkg/binding/` | per-allocation 绑定 |

### 网络

| 路径 | 看什么 |
|---|---|
| `components/ingress/pkg/proxy/header.go` | 路由/续期/逐跳头常量 |
| `components/ingress/pkg/proxy/` | 路由解析、WebSocket、错误映射 |
| `components/ingress/pkg/proxy/connectivity/` | 连接分类与 DEGRADED |
| `components/egress/pkg/policy/policy.go` | 规则模型 |
| `components/egress/pkg/policy/always_rules.go` | 平台覆盖与端口地板 |
| `components/egress/pkg/dnsproxy/` | DNS 过滤与地址记录 |
| `components/egress/pkg/nftables/` | 集合与原子替换 |
| `components/egress/pkg/credentialvault/` | vault 与绑定 |
| `components/egress/mitmscripts/` | 请求改写与路径守卫 |
| `components/egress/pkg/subject/` | fast-sandbox subject 状态机 |

### 采集与观测

| 路径 | 看什么 |
|---|---|
| `components/nodeagent/pkg/source/containerlogs/` | kubelet 日志跟随 |
| `components/nodeagent/pkg/source/syscalls/` | eBPF 系统调用记录 |
| `components/nodeagent/pkg/sink/oss/` | 对象存储投递 |
| `components/nodeagent/pkg/marker/` | 完整性标记 |
| `components/nodeagent/pkg/pipeline/` | 有界队列与背压 |
| `server/opensandbox_server/services/diagnostics.py` | 诊断描述符 |
| `server/opensandbox_server/api/devops.py` | 纯文本排障路由 |

### SDK 与工具

| 路径 | 看什么 |
|---|---|
| `sdks/sandbox/python/src/opensandbox/sandbox.py` | `create` / `connect` / 高级方法 |
| `sdks/sandbox/python/src/opensandbox/services/` | 生命周期、execd、egress 三套客户端 |
| `sdks/sandbox/python/src/opensandbox/pool*.py` | 客户端池与状态存储 |
| `sdks/code-interpreter/python/` | 代码解释器高层封装 |
| `sdks/mcp/sandbox/python/src/opensandbox_mcp/server.py` | MCP 工具面 |
| `cli/src/opensandbox_cli/` | `osb` 命令实现 |

## 规模参考

按 `go/py/ts/java/cs` 计（`wc -l` 汇总，含注释与生成物）：

| 区域 | 量级 |
|---|---|
| `sdks/sandbox` | ~20 万行 |
| `components/egress` | ~9.6 万行 |
| `server` | ~8.4 万行 |
| `components/execd` | ~6.3 万行 |
| `components/nodeagent` | ~2.4 万行 |
| `components/ingress` | ~1.5 万行 |
| `cli` | ~1 万行 |

SDK 体量大主要来自五种语言与生成代码，不代表逻辑复杂度；`kubernetes/` 目录行数中等但信息密度最高（CRD 语义 + reconcile 不变量）。

## 文档地图

| 目录 | 内容 |
|---|---|
| `docs/architecture/` | 分层架构：control-plane / data-plane / network / fast-sandbox |
| `docs/api/` | 契约总览与实现兼容性 |
| `docs/getting-started/` | 安装、配置 |
| `docs/guides/` | 21 篇运维指南（多租户、暂停、vault、池、隔离会话、遥测…） |
| `docs/deployment/` | Kubernetes 部署顺序与配置 |
| `docs/examples/` | 30+ 集成示例（Claude Code、Codex、Playwright、ADK、LangGraph…） |
| `oseps/` | 24 份设计提案（见附录 B） |
| `specs/` | 四份 OpenAPI 契约 + 契约测试 |
