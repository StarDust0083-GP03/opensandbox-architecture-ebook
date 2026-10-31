# Kubernetes 后端：BatchSandbox 与恒定时间交付

Docker 后端回答的是「一台机器上怎么放很多沙箱」。Kubernetes 后端要回答的是另一个问题：**在集群里，创建沙箱的延迟里有多少是调度器的？**

答案不低。创建一个带调度约束的 Pod，要经过 API server 写入、调度器决策、kubelet 拉镜像、容器启动、就绪探针。这条链路的每一段都可能失败或变慢，而 Agent 场景希望创建是一次「认领」，不是一次「部署」。

OpenSandbox 的 Kubernetes 后端因此分成两层：一个标准的 operator（三个 CRD + 三个 reconciler），和一条预热池路径（`Pool`）。这一篇讲这套设计，重点是池子的容量模型，因为它决定了「快」是怎么来的。

## 三个 CRD

| CRD | 作用 | 关键字段 |
|---|---|---|
| `BatchSandbox` | 描述一个或多个相同沙箱 | `replicas`、`template`、`poolRef`、`expireTime`、`pause`、`taskTemplate` |
| `Pool` | 维护预热 Pod 的容量 | `template`、`capacitySpec`、`scaleStrategy`、`recycleStrategy` |
| `SandboxSnapshot` | 暂停/恢复与公开快照的记录 | 目标 `BatchSandbox` 名；状态里带镜像 digest |

三个 reconciler 各管一个，遵循标准 Kubernetes 模式：watch 期望状态，和观测到的 Pod 做 diff，用幂等操作收敛。

它们之间不互相调用，只通过两个契约通信：

**分配注解。** Pool reconciler 在 `BatchSandbox` 上写 `sandbox.opensandbox.io/alloc-status`（已分配的 Pod、`poolRef`、generation）和 `alloc-release`；`endpoints` 注解携带解析好的地址，供服务器读取。Pod 上带 `sandbox.opensandbox.io/pool-name` 和 `pool-revision` 标签。

**任务派发。** batch reconciler 内部有一个 `TaskScheduler`，把任务分配给 Pod，并调用每个沙箱 Pod 内的 task-executor HTTP 服务。

用注解而不是新 API 做进程间通信，符合 Kubernetes 的一贯做法：可观测、可调试、可以用 `kubectl` 直接看。代价是注解没有 schema，改字段要小心兼容。

## Pool 的容量模型

这是本章最值得细看的部分。Pool 有**两级容量**：

```text
poolMin ─────────────── poolMax        总池：拥有的一切 Pod（含已分配）
        bufferMin ─── bufferMax        缓冲区：Running + Ready 且未分配的 Pod
```

- **缓冲区**是分配真正能拿走的东西，控制器持续把它收敛到 `[bufferMin, bufferMax]`：分配抽干缓冲区就扩容，释放后超过 `bufferMax` 就缩容。
- **总池**由 `poolMin`/`poolMax` 界定。还在启动的 Pod 计入总数，但**永远不会被算作可用缓冲区**。

第二句话是整个设计的关键：**池子不承诺它交不出来的容量**。如果只看总数，一个刚创建、还没 Ready 的 Pod 会被算作可用，分配请求就会拿到一个还不能用的沙箱。

扩容是节流的：每次 reconcile 最多创建或删除 `maxUnavailable` 个 Pod（默认 25%，绝对数或百分比）。文档的理由是避免「一波分配引发一场 Pod 雪崩」。这条限制和「快」看起来矛盾，其实不矛盾：缓冲区保证稳态快，节流保证突发时集群不被自己打垮。

## 分配与回收

**分配**是一次常数时间的认领：Pool reconciler 把 Pod 调度给等待中的 `BatchSandbox`，然后把分配结果写进注解。没有新的调度往返。

**回收**由 `recycleStrategy` 决定：

| 策略 | 行为 | 代价 |
|---|---|---|
| `Delete`（默认） | 换一个全新 Pod | 每次分配都从模板的干净状态开始 |
| `Restart` | 原地重启容器 | 比换 Pod 便宜，进程状态被清 |
| `Noop` | 原样归还 | 最快，但文件系统和进程状态会泄漏到下一次分配 |

`Noop` 的存在很坦诚：它是给「沙箱内部自己负责清理」的场景留的口子，并且文档明说了泄漏风险。这里能看到一个成熟设计的特点——**把危险的选项保留下来，但把代价写在文档第一行**。

## 池化带来的三个 API 约束

因为池里的 Pod 在任何请求之前就存在了，生命周期 API 无法把请求级状态注入进去。文档因此列了三条硬约束：

1. **`entrypoint`/`env` 变成分配后下发的 task**，而不是改写 Pod。所以在 Pod spec 里看到的是模板原本的 entrypoint，这是预期行为，不是 bug。
2. **`volumes` 和 `networkPolicy` 不能与 `poolRef` 同时使用**，必须提前配在 Pool 模板里。
3. **池 Pod 必须满足 task 契约**（有 in-pod task-executor、bootstrap entrypoint、装好 execd），否则生命周期 API 用不了它们。

这三条是「预热」与「每请求定制」冲突后的落点，也是第 1 篇说的需求冲突在这里的具体形态。

## 容量耗尽：429 而不是排队

当没有可用的池槽位时，控制器会在等待的 `BatchSandbox` 上写入 `PoolAllocationPending` 条件（reason `PoolCapacityExhausted`），每 5 秒重查一次。服务器侧则等待一个有界窗口，窗口内没等到就返回**可重试的 `429`**。

这个设计有三个值得学习的细节：

- 失败被编码成一个条件，而不是只写日志。运维可以 `kubectl describe` 看到原因。
- 服务器不无限等。它把「池满」当成一种正常的、可重试的失败，而不是系统故障。
- 窗口期内释放的槽位仍然可以满足这个请求——所以不是「立刻拒绝」，而是「有限等待后再拒绝」。

## 创建为什么会「同步成功」

生命周期 API 整体是异步的（返回 `Pending`），但 Kubernetes 后端有一个例外行为：使用 `BatchSandbox` 或 agent-sandbox provider 时，服务器会等工作负载进入 `Running` 或 `Allocated` 后才返回成功。

配置项在 `[kubernetes]`：

| 配置 | 默认 | 含义 |
|---|---|---|
| `sandbox_create_timeout_seconds` | 60 | 创建等待上限 |
| `pool_acquisition_timeout_seconds` | 30 | 池容量耗尽的累计等待上限 |
| `sandbox_create_poll_interval_seconds` | 1.0 | 无通知时的兜底轮询间隔 |

实现上用了已有 watch：工作负载变化会唤醒对应的等待请求，并直接评估事件自带的对象，不额外读一次 API。没有订阅能力的 provider 继续轮询。

文档特意说明了一点：**这只保证工作负载 Running，不保证你的应用就绪**。客户端仍然要用 execd 的 `/ping` 或自己的健康检查确认可用。

## 状态怎么读

`BatchSandbox` 的 `status.phase` 和 `conditions` 是两套视角：

- **phase 报运行时健康**：`Pending`（还没有 Running+Ready 的 Pod）、`Succeed`（至少一个 Pod Running+Ready，这是稳态，**不是任务完成**）、`Pausing`/`Paused`/`Resuming`、`Failed`（终态运行时失败）。
- **conditions 解释原因**：`Ready`、`Progressing`、`Paused`、`PauseFailed`、`ResumeFailed`、`PodFailed`、`PoolAllocationPending`。

文档给了两条使用纪律：只在条件 `status: "True"` 时计数；读更新后的 status 前先比较 `status.observedGeneration` 与 `metadata.generation`。第二条尤其重要——否则你会把上一代的状态当成本次操作的结果。

## 暂停与恢复：把 rootfs 变成镜像

Kubernetes 后端的暂停不是 `docker pause`。它的流程是：

```text
pause
  → 创建内部 SandboxSnapshot
  → 在源节点上跑 commit Job，把容器 rootfs 提交成 OCI 镜像并推送 registry
  → 释放 Pod 与池槽位
  → 状态 Paused

resume
  → 复用同一个 BatchSandbox，把 template 改写成最新快照镜像
  → 重建运行时
  → 状态 Running
```

四个约束必须记住：

- **只支持单副本**（`spec.replicas=1`）。内部快照记录的是一个源 Pod 的容器镜像，无法表达每副本状态。
- **`Succeed` 才代表可恢复**。对 `rootfs-v1`，只有在 commit Job 为每个容器都报告了镜像 digest 后才发布 `Succeed`；读不到结果（例如 commit Pod 被提前删除）会标 `Failed` 并给出 `CommitResultUnavailable` / `CommitResultIncomplete`，此时运行时 Pod **保留**，沙箱报 `PauseFailed`，可以重试。
- **Pod 终止可以晚于 `Paused`**。这个窗口里提交 resume，控制器会等旧 Pod 消失再建新的；带删除时间戳的 Pod 不再贡献新的失败条件。
- **内存不保留**（除非启用 QEMU-in-runc 模式）。rootfs 模式恢复的是文件系统，进程会重启。

还有一个容易踩的时间学问题：暂停请求被接受、或快照处于 `Pending`/`Committing`，都**不构成持久性保证**。只有镜像真的推到 registry 才算。文档还提到一条「尽力而为」的运维救急路径——image-committer 有时能从 containerd 直接读已停止的容器，但这**不是支持的持久性契约**。

## 性能数怎么读

文档给的对比很醒目：

| 场景 | 总耗时 |
|---|---|
| agent-sandbox（并发 1 / 10 / 50） | 76.4 s / 23.2 s / 33.9 s |
| BatchSandbox（启用池化） | 0.92 s |

同一个「交付 100 个沙箱」的任务，差了两个数量级。为什么？

- agent-sandbox 走的是标准 Pod 调度链路，每次创建都要等调度、拉镜像、启动、就绪；
- BatchSandbox 启用池化后，创建是认领已有 Pod，几乎没有调度开销。

这个对比**不能**被读成「agent-sandbox 慢」。它说明的是「预热 vs 不预热」的差异，而 agent-sandbox 是作为外部 provider 接进来的，它有自己的池化能力（SIG 的 Agent Sandbox 项目本身也提供池）。文档给的是 OpenSandbox 这条集成路径上的实测，读者要自己判断可比性。

## 本章要点

- 三个 CRD：`BatchSandbox`、`Pool`、`SandboxSnapshot`；三个 reconciler 通过注解和 task 派发协作。
- Pool 是两级容量：缓冲区（可分配）与总池（含启动中）。启动中的 Pod 不计入可用容量。
- 分配是常数时间认领；回收有 `Delete`/`Restart`/`Noop` 三档，各有明确代价。
- 池化带来三条 API 约束：entrypoint/env 走 task、卷与网络策略不能与 `poolRef` 同用、池 Pod 必须满足 task 契约。
- 池满返回可重试的 `429`，并留下 `PoolAllocationPending` 条件。
- 暂停 = 提交 rootfs 镜像 + 释放 Pod，单副本限制，`Succeed` 才算可恢复。
- 100 个沙箱 0.92 s 是「池化 vs 非池化」的对比，不是两种项目的性能排名。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `kubernetes/apis/sandbox/*/batchsandbox_types.go` | `BatchSandboxSpec` 全字段与注释 |
| `kubernetes/internal/controller/batchsandbox_controller.go` | Pod 扩缩、任务调度、过期、暂停交接 |
| `kubernetes/internal/controller/pool_controller.go` | 缓冲区收敛、分配注解、模板 revision |
| `kubernetes/internal/controller/sandboxsnapshot_controller.go` | commit Job 与镜像结果记录 |
| `docs/architecture/control-plane/operator.md` | 容量模型、回收策略、状态解读 |
| `docs/getting-started/configuration.md` | 创建等待与池容量耗尽的配置 |
