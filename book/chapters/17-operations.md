# 运维与失败：这套系统承诺什么，不承诺什么

前面十六篇讲的是「它怎么工作」。这一篇讲它在坏掉的时候长什么样。

判断一个基础设施项目的成熟度，最有效的办法不是看功能表，而是看它敢不敢写清「这里会中断」「这里不保证」「这里要人工」。OpenSandbox 的文档在这方面出乎意料地直接，这一篇基本是在整理它的自我限制清单，并补上我的解读。

## 部署顺序不是建议，是依赖

Kubernetes 部署有固定顺序：

```text
base → opensandbox-controller → fast-sandbox* → ingress-gateway → opensandbox-server → 可选组件
```

每一步的理由都写在文档里：

| 步骤 | chart | 为什么在这个位置 |
|---|---|---|
| 1 | `base` | 拥有 `sandbox.opensandbox.io` 与 `sandbox.fast.io` 的 CRD 与 RBAC，其他一切都依赖这些对象存在；每集群装一次 |
| 2 | `opensandbox-controller` | reconcile 你和服务器创建的 `BatchSandbox`/`Pool`/`SandboxSnapshot` |
| 3 | `fast-sandbox`（可选） | 必须在 server 之前：服务器的 fsb 配置指向 FastPath gRPC endpoint（`fast-sandbox-fastpath...svc:9090`），启动时就开始 watch `sandbox.fast.io` 对象 |
| 4 | `ingress-gateway` | **Kubernetes 部署必需**：沙箱 Pod 只有 ClusterIP，客户端流量必须经网关；且要在 server 之前装，服务器才能在首次安装时就宣告它 |
| 5 | `opensandbox-server` | 生命周期 REST API |
| 6 | `opensandbox-node-agent`（可选） | 顺序无关 |

第 4 条最容易被跳过，后果也最直接：跳过网关，沙箱创建成功但谁也访问不到。文档把它写成「required for Kubernetes deployments」而不是「recommended」。

用 umbrella chart 时这个顺序由 chart 处理。注意它需要先 `helm dependency build opensandbox`——`charts/` 目录在仓库里是 git-ignored 的，每次重建。chart 从 `release-X.Y.Z` 标签的 checkout 安装，**不单独发布 chart 包**。

## 服务器的高可用承诺有多窄

这是全文档里最值得逐字读的一段：

> The chart defaults to `server.replicaCount: 1`. Keep one active Lifecycle Server unless you deliberately use the PostgreSQL-backed Kubernetes public snapshot topology... That exception coordinates public snapshots only; it does not provide general multi-replica Server HA. The Server Deployment uses the `Recreate` strategy so an upgrade stops the active Server before starting its replacement; expect a brief API interruption during upgrades.

拆开来是四个明确事实：

1. **默认单副本**，升级时会短暂中断 API（`Recreate` 策略：先停后起，不并存）。
2. **多副本只有一个合法场景**：PostgreSQL + Kubernetes runtime，且**只协调公开快照**。
3. **SQLite 与 Docker 快照执行不支持这个多活拓扑**。
4. 文档在别处还写了一句更直白的：「General load-balanced lifecycle HA is not supported yet — and is not pretended.」

第 4 条的语言很少见。大多数项目会把「部分多活」写成「高可用」，这里选择把它写成它本来的样子。

顺带一个对比：Controller 的 chart 默认**开启** leader election（`leaderElection.enabled: true`），因为它本来就是 Kubernetes 风格的控制器；而生命周期服务器不是，它没有 leader election，只有「一个副本」这个操作约定。两种风格并存，说明作者没有为了统一而统一。

## Fast Sandbox 的失败表

Fast Sandbox 的文档给了一张很干净的失败矩阵：

| 失败 | 影响 | 恢复 |
|---|---|---|
| 控制面重启 | 短暂失去变更能力；运行中沙箱不受影响 | CR 驱动重放意图 |
| Fastlet Pod 丢失 | 该 Fastlet 上的沙箱中断 | 按每沙箱 `failurePolicy`，在 `recoveryTimeoutSeconds`（默认 60 秒）后处理；默认人工 |
| 服务器重启 | 沙箱无影响；未完成操作从持久记录继续 | 同一套观察-收敛 |
| 制品 store 故障 | 新启动降级为直接拉取；运行中沙箱不受影响 | store 恢复后 DART 继续服务 |

第二行是唯一的「会丢东西」的情况，而且是**默认人工**。这是一个刻意的选择：自动把沙箱迁到别的节点，需要先决定「未持久化的写入怎么办」，而这个决定属于业务，不属于平台。

## 容量规划：三处容易被忽略的账

**池容量。** `bufferMin`/`bufferMax` 决定稳态快，`poolMin`/`poolMax` 决定上限。配小了会频繁 `429`；配大了会白占资源。文档给的调节旋钮是 `maxUnavailable`（默认 25%）和池的模板 revision 滚动。

**快照的 registry 与保留。** Kubernetes 后端的快照镜像要推到 registry，凭据配在 **controller manager 启动参数**上（`--snapshot-image-uri-template` 之类的也在那里），**不在 `~/.sandbox.toml`**。文档专门强调这一点，因为它和大多数人的直觉相反：快照是控制器做的，不是服务器做的。

另外：**删除快照记录不会删除已推送的镜像**。保留与清理是运维策略，被有意留在沙箱生命周期之外。这意味着如果你不主动清理，registry 会一直涨。

**节点状态盘。** Firecracker 的每沙箱 rootfs 是 XFS reflink 的 CoW 副本，所以节点状态根必须在 XFS + reflink 上。文档给了准备步骤（新建空盘 `mkfs.xfs -m reflink=1`、挂载、`blkid`、写入 fstab），也给了没有额外设备时的 `scripts/fast-sandbox-state-root-setup.sh`（用 `fallocate` 预留空间建 loop 文件系统，拒绝已存在的 backing file、符号链接路径、已挂载目标与非空挂载点，失败时保留文件供检查而不是冒险删除）。

容量口径也写得很实：要按「每个被缓存模板的 rootfs + guest 内存快照 + 活跃沙箱可写数据 + 缓存开销」算，起步留 50% 余量，并保持 **> 10 GiB 空闲**（默认就绪地板）。

还有一条运维动作容易做错：如果节点已经在跑沙箱，你新挂了状态盘，**已运行的沙箱可能还看着旧文件系统**。正确做法是先 drain 该节点沙箱，再重启 runtime Pod；或者在部署 runtime DaemonSet **之前**就把盘准备好。

## 三个「已知不可靠」的明确声明

文档里有三处主动说明「这块还没被验证到位」，我认为这是这一篇最重要的内容。

**1. Node Agent 的资源默认值没有基准。** 数据面文档写明：采集与投递已实现，但生产的资源默认值仍在等 benchmark 与 soak 结果（OSEP-0019），「把 chart 的资源值当作起点」。

**2. Fast Sandbox 并发 20 的创建不可靠。** `Performance` 页写明：并发 20 的延迟分布**只含成功的尝试**，48 次 `SandboxRateLimitException`（HTTP 429 / Fastlet `CapacityRejected`）与 1 次就绪超时被排除；有 4 个测试 CR 卡在删除中，原因是 assignment annotation/status projection 冲突。文档的结论是：这次运行不建立「可靠的 20 路接纳」或「可持续 RPS」，实现层面的原因需要单独修，**换宿主磁盘解决不了**。

**3. 同节点恢复的 62 秒无人解释。** 运行时恢复是几十毫秒，但 SDK 侧恢复中位 62.66 秒。文档明确说：日志显示路由在恢复后立刻发布，大部分时间在 endpoint/guest 可用性与客户端等待上；这次测量**没有隔离出**是网络、网关、endpoint 解析还是重试机制造成的差距，也明确禁止把运行时数字当作 SDK 恢复数字。

这三条的共同点是：**它们都给出了「不要用哪个数字做承诺」**。这比多写三个功能有用。

## 升级与镜像可信

两条与升级相关的实践：

**镜像按 digest 固定。** 发布镜像在三个官方 registry（Docker Hub、GHCR、阿里云 ACR）以同名组件发布；带标签的发布镜像用 Cosign 做无密钥签名并带 provenance 证明。生产要按 digest 固定，并按 `docs/community/release-verification.md` 对照 OpenSandbox 的 GitHub Actions 身份验证。

**旧镜像的坑要记住。** 单机网络文档提过一个具体例子：`server/v0.2.3` 把内部 IP 解析硬编码在两条代理路径里，只设 `resolve_internal = false` 救不回来，SDK 就绪检查会超时；修好的镜像是 `release-1.1.0`。文档还提醒 `server/v*` 这类冻结标签不再标识较新的服务器发布，现在用统一发布标签。

这段的价值在于它示范了「兼容性说明要写到版本号一级」。

## 排障顺序建议

把第 16 篇的五层证据和这一篇的失败矩阵合起来，我建议的排障顺序是：

1. **看状态**：`GET /sandboxes/{id}` 的 `state`/`reason`/`message`，先分清是「没创建」「创建了但没就绪」「就绪但访问不到」。
2. **看条件**：Kubernetes 上 `kubectl describe batchsandbox`，特别是有没有 `PoolAllocationPending`；Fast Sandbox 上看 `sandbox.fast.io` CR 的 `status.runtime`。
3. **看 endpoint**：`GET .../endpoints/{port}` 的形态是否符合预期（直连/网关/代理），头是否带全。
4. **看数据面**：`/ping` 是否 200；不行再取 `diagnostics/logs`，注意 `truncated`。
5. **看指标与日志**：egress 的「拒绝」与「解析失败」分开看；ingress 的 `network-readiness` 只看它的影子信号含义。
6. **查已知限制**：并发创建 429、池满、暂停后 vault 为空、`404` 不代表进程已死。

第 6 步经常被跳过，而它往往是最快的答案。

## 本章要点

- 部署顺序是依赖：CRD → controller → (fast-sandbox) → ingress-gateway → server；K8s 下沙箱 Pod 只有 ClusterIP，缺网关等于访问不到。
- 服务器默认单副本、升级会短暂中断；多副本只允许 PostgreSQL + K8s runtime 且只协调公开快照；SQLite/Docker 不支持。
- Controller 默认开启 leader election；生命周期服务器没有，靠单副本约定。
- Fast Sandbox 控制面单副本是设计立场；Fastlet 丢失默认人工恢复（60 秒后按 `failurePolicy`）。
- 三处账要单独算：池容量、快照 registry 与保留（删记录不删镜像）、节点 XFS reflink 状态盘与 10 GiB 地板。
- 文档主动声明三处「未验证到位」：node agent 资源默认值、并发 20 创建、同节点恢复 62 秒。
- 镜像按 digest 固定并做 Cosign 验证；`server/v*` 冻结标签不再代表新版本。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `docs/deployment/index.md` | 部署顺序、单副本约束、PostgreSQL 拓扑 |
| `manifests/charts/opensandbox/` | umbrella chart 与子 chart 依赖 |
| `manifests/charts/controller/values.yaml` | leader election 与快照 registry 配置 |
| `manifests/charts/node-agent/` | 采集目标与资源默认值 |
| `docs/guides/pause-resume.md` | registry/Secret 配置与暂停的中断语义 |
| `docs/community/release-verification.md` | 镜像签名与验证流程 |
