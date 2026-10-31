# 完结篇：跟着一次请求走完全书

这一篇把全书串一遍。场景是第 1 篇那个修 bug 的编码 Agent：它要为一次任务申请一个沙箱，跑测试，装一个依赖，改代码，再跑一次，最后交 diff。

## 请求：`POST /v1/sandboxes`

客户端带着 `OPEN-SANDBOX-API-KEY`，body 里是一份意图：

```json
{
  "image": { "uri": "python:3.12" },
  "entrypoint": ["sleep", "infinity"],
  "resourceLimits": { "cpu": "2", "memory": "2Gi" },
  "timeout": 1800,
  "networkPolicy": {
    "defaultAction": "deny",
    "egress": [
      {"action": "allow", "target": "pypi.org"},
      {"action": "allow", "target": "*.github.com"}
    ]
  },
  "credentialProxy": { "enabled": true },
  "lifecycle": { "idlePolicy": { "idleTimeoutSeconds": 600, "onIdle": "pause", "wakeOnAccess": true } },
  "metadata": { "task": "fix-issue-418" }
}
```

这份 JSON 里每个字段都对应前面某一篇的一个机制：

| 字段 | 由谁处理 | 在哪一篇 |
|---|---|---|
| `image` | 运行时后端创建容器/工作负载 | 6 / 7 |
| `entrypoint` | 容器入口或分配后的 task | 6 / 7 |
| `resourceLimits` | 后端翻译成容器限额或 Pod 资源 | 6 / 7 |
| `timeout` | 绝对 TTL + 过期定时器/`expireTime` | 13 |
| `networkPolicy` | egress sidecar 或共享 egress subject | 11 |
| `credentialProxy` | 挂 egress 并启用 MITM 与 vault | 12 |
| `idlePolicy` | 空闲扫描器 + 唤醒路径 | 13 |
| `metadata` | 标签/注解，可被列表过滤 | 4 / 6 |

## 第 1 毫秒：控制面

`RequestIdMiddleware` 生成或透传请求 ID。`AuthMiddleware` 校验 key：单租户比对 `server.api_key`，多租户交给 `TenantProvider` 并把租户命名空间写进 `ContextVar`（第 14 篇）。

路由进入 `api.lifecycle.create_sandbox`，四步校验：`extensions` 合法性、renewal 与 extensions 的冲突、`networkPolicy` 归一化、`snapshotId` 解析（这里没有 snapshot，跳过）。然后委派给 `sandbox_service`（第 4 篇）。

因为 `runtime.type = "kubernetes"`，实际对象是 `CompositeSandboxService`；因为没有 `templateId`，请求走到 `KubernetesSandboxService`（第 5 篇）。

## 第一个 100 毫秒：认领容量

服务器创建 `BatchSandbox`，带 `expireTime`、Pod 模板合并后的资源与平台约束、egress sidecar 的追加、以及网络策略的注解。

如果集群里有 `Pool`，控制器会把一个**已经 Running+Ready** 的预热 Pod 标注给这个 `BatchSandbox`，写 `alloc-status` 注解；`entrypoint` 作为分配后的 task 下发，而不是改写 Pod。如果池空了，控制器写 `PoolAllocationPending`（reason `PoolCapacityExhausted`），服务器在有界窗口内等待，超时返回可重试的 `429`（第 7 篇）。

这条路径之所以是毫秒级，是因为调度、拉镜像、启动都发生在**请求之前**。这就是第 1 篇那条「快在已经存在的容量上」的兑现。

## 就绪：从 `Pending` 到 `Running`

服务器等工作负载进入 `Running`/`Allocated`（默认 60 秒上限），然后返回创建响应。客户端拿到的是一个 `Pending` 或 `Running` 的沙箱对象，带 `state`/`reason`/`message`(第 4 篇)。它至此不再需要轮询创建接口，而是从这个沙箱对象读所有后续状态。

同时发生的还有两件事：

- egress sidecar 已经把 DNS 与 nftables 规则装好，默认拒绝，只放行 pypi 与 github（第 11 篇）；
- execd 在容器里启动，等待 per-allocation 绑定；绑定未完成前，除 `/ping`/`/ready` 外全部返回 `503`（第 9 篇）。

## 接入：解析 endpoint

客户端要跑命令，于是 `GET /sandboxes/{id}/endpoints/44772`。

服务器返回一个地址 + 需要的头。在 Kubernetes + ingress 部署下，通常是网关地址加 `OpenSandbox-Ingress-To: <id>-44772`；开了 `secureAccess` 时还带 endpoint 凭据；Fast Sandbox 的返回则是一个签名的 `f1.` scope（第 8、10 篇）。

**这一步的返回值是凭据，不是普通字符串。** 客户端要原样保留，不能丢头、不能截断路径。

## 干活：一次命令执行

客户端 `POST /command`，body 指定 argv 或 shell 语法。execd 执行并回 SSE：

```text
data: {"type":"init"}
data: {"type":"stdout","text":"running 42 tests...\n"}
data: {"type":"stderr","text":"FAILED test_retry\n"}
data: {"type":"execution_complete","exit_code":1}
```

客户端必须按第 9 篇那三条语义解析：拼接事件还原输出（行尾原样）、`execution_complete` 的 `exit_code` 才算结论、流在终止事件前结束就当作不确定。

对应地，如果客户端是老版本 SDK 且 classpath 里有不兼容的 kotlinx-serialization，它可能解不出这个字段而挂住——这就是 `EXECD_SSE_LEGACY_COMPAT` 存在的原因。

## 装依赖：出向策略与凭据

Agent 要 `pip install`。请求发往 `pypi.org`：

1. DNS 被重定向到 sidecar 的 DNS 代理，域名命中 allow 规则，正常解析并记录地址（第 11 篇）；
2. nftables 放行该地址，租约有时限；
3. 如果要访问的是需要认证的内部制品库，请求被 mitmproxy 拦截，绑定匹配后注入认证头；沙箱里的 `PIP_INDEX_URL` 里只有占位符或假值（第 12 篇）。

如果 Agent 尝试访问 `169.254.169.254` 或者 `metadata.internal`：DNS 返回 `NXDOMAIN`，或者包被 nftables 丢弃。平台覆盖规则永远优先于租户规则——即使请求里写了 allow（第 11 篇）。

## 访问中途：续期与空闲暂停

这个任务跑了 40 分钟。期间客户端每隔一段时间唤醒沙箱访问一次：

- 每次经网关的请求都会发布一次 **renew-intent**，沙箱的绝对过期时间被延长（第 13 篇）；
- 如果客户端停了 10 分钟（超过 `idleTimeoutSeconds: 600`），空闲扫描器把它暂停：Kubernetes 后端会提交 rootfs 镜像、释放 Pod；
- 下一次访问时，`wakeOnAccess: true` 让它自动唤醒，但**地址变了**，客户端必须重新解析 endpoint（第 13、10 篇）。

如果部署的是 Fast Sandbox，唤醒可能是检查点恢复，并且在**另一台宿主机**上——重新解析不是可选动作。

## 交付：快照还是 diff

任务结束有两种收尾：

**交 diff**：直接用文件 API 从工作区读改动，然后删除沙箱。删除是异步的：`DELETE` 返回 `204`，之后 `GET` 会看到 `Stopping`，清理完成变 `404`。

**保留现场**：`POST /sandboxes/{id}/snapshots` 创建快照。只有 `Running` 源可以创建；记录从 `Pending → Creating → Publishing → Succeeded`；只有 `Succeeded` 之后才是一个可复用的起点，从它创建出的是**新沙箱 ID**（第 13 篇）。

注意这里有一个不对称：快照成功之后，沙箱本身的后续写入**不会**被自动快照；进程退出、OOM、驱逐都不会自动保存。想要可复现环境，就得显式做快照——这条边界在第 13 篇被反复强调，因为它是「暂停能不能当备份用」的唯一答案。

## 出问题时，你会在哪里看到

| 现象 | 第一现场 | 哪一篇 |
|---|---|---|
| 创建 429 | `PoolAllocationPending` 条件、`PoolCapacityExhausted` | 7 |
| 卡在 `Pending` | `status.reason`、Pod 事件 | 4 / 7 |
| 命令没输出 | 缺 `execution_complete`，或流被提前切断 | 9 |
| 装依赖失败 | egress：域名被拒（DNS）还是包被丢（nft） | 11 |
| 认证 401/403 | vault 未注入、绑定未匹配、路径守卫拒绝 | 12 |
| 恢复后连不上 | endpoint 变了，没重新解析 | 10 / 13 |
| 恢复后凭据失效 | Kubernetes 后端 vault 为空，需重新注入 | 12 / 13 |
| 日志消失 | 沙箱已删，需要 node agent 才能留证据 | 16 |

这张表是全书的索引：每一行都能追到一个具体机制，而不是一句「看日志」。

## 如果我要改这块代码

按改动类型给三条路径：

**加一个后端。** 实现 `SandboxService` 的 13 个方法，给 ID 一个前缀，在 `factory.py` 或 `Composite` 里注册，在兼容性文档里写清不支持的能力（第 5 篇）。不要动 API 与 schema。

**改出向策略。** 从 `components/egress/pkg/policy/` 的规则模型开始，注意优先级（平台 deny → 平台 allow → 用户）、端口地板的补集语义、以及 fast-sandbox profile 的差异（第 11 篇）。任何新增的「放行」路径都要先想清楚它是否绕过了 deny-first。

**加一个执行能力。** 先看 execd 的分层：命令/文件/PTY 是不同子系统，隔离会话是另一层命名空间（第 9 篇）。新增能力要决定它属于哪一层、是否需要在契约里、以及在池化复用时如何被 per-allocation 绑定重置。

## 收尾

回到开头那个问题：`POST /v1/sandboxes` 之后发生了什么。

答案是六件事同时发生：控制面校验并委派、运行时认领已有容量、数据面准备好执行接口、网络装上默认拒绝的策略、凭据被留在沙箱之外、生命周期开始倒计时。它们分别由四个进程、三个后端、四份契约保证，并且每一处不确定都被写成了可检查的字段。

这套系统的价值不在于它有多少功能，而在于它把「承诺」和「不承诺」分得很清。第 18 篇列的那些取舍，本质上都是同一件事的不同侧面：**在能保证的地方给出强保证，在不能保证的地方留下可验证的痕迹。**

读到这里，如果你要做一个类似的执行层，我希望你带走的是这句话，而不是任何具体实现：

> 沙箱平台的难点从来不是把容器跑起来，而是让「隔离」「快」「可解释」这三件事在同一个请求里同时成立，并且诚实地告诉使用者哪一件打了折。

## 本章要点

- 一次请求会依次穿过：认证与租户 → 校验与委派 → 容量认领 → 就绪 → endpoint 解析 → 命令/文件/代码执行 → 出向策略与凭据 → 续期/空闲暂停 → 快照或删除。
- 每个阶段都有一个「第一现场」：状态字段、条件、事件、指标或日志。
- 三条改动路径：加后端（实现接口 + 前缀 + 文档）、改出向（优先级与 deny-first）、加执行能力（分层与绑定重置）。
