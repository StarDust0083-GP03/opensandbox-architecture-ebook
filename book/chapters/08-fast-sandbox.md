# Fast Sandbox：模板、Fastlet 与 Firecracker

前三篇讲的两种后端都在「容器」这个单位上思考。Fast Sandbox 换了一个单位：**模板**。

在 Fast Sandbox 里，一个沙箱在创建请求到来之前就已经被决定了大半：运行时、镜像、entrypoint、环境变量、资源形状都固定在模板里；创建请求只表达意图——用哪个模板、谁在用、用多久。这一篇讲这个模型，以及它为什么需要一整套外部的控制面。

## 它在架构里的位置

Fast Sandbox 不是新的 `runtime.type`。`Runtime.type = "kubernetes"`，`CompositeSandboxService` 把带 `templateId` 的创建请求分给它，把 `fsb-` 前缀的既有沙箱操作也分给它。

控制协议是 gRPC（FastPath v2，端口 9090）。但 gRPC 只到 FastPath 为止：FastPath/Reconciler 到 Fastlet 的控制协议是 JSON over HTTP（`/api/v2/fastlet/*`）。持久化状态不在任何进程的内存里，而在 `sandbox.fast.io` 的 CR 里。

外部平台（FastPath、Fastlet、CRD）由 `opensandbox-group/fast-sandbox` 拥有，OpenSandbox 只提供适配层与 endpoint 集成。这条边界很重要：**本书讨论的是适配与被观察的语义，不是那套平台内部的实现**。

## 九个组件各自的职责

| 组件 | 职责 | 设计目标 |
|---|---|---|
| FastPath | gRPC 控制面，承担全部变更与实时操作 | 内存内放置决策，创建常数时间；可重启，因为持久状态在 CR 里 |
| Reconcilers | 把 CR 驱动成现实：建模板、跑池、放置沙箱、完成快照 | 观察即收敛，无 leader election |
| `sandbox.fast.io` CRD | 每个沙箱的意图、放置、策略绑定、观测结果 | 意图能活过任何组件失败 |
| Fastlet | 预热 Pod，一个 Fastlet 里放多个沙箱 | 密度与隔离：网络槽位预置，egress 与 DNS 共享 |
| runtime agent | 每节点一个，经 unix socket 管 Firecracker VM 生命周期 | 节点自治：控制面委派，节点自检后才收活 |
| DART | 节点间 P2P 制品分发 | 摊薄源站：每个块全集群大约只从源站取一次 |
| egress process | 每 Fastlet 一个，服务 N 个沙箱的出向策略 | 无 per-sandbox sidecar 也能 fail-closed |
| 模板构建器 | OCI 镜像 → golden image（含 guest 接线与 execd） | 负载在任何请求之前就固定 |
| janitor | 扫掉失败操作的孤儿资源 | 收敛负责补齐，janitor 负责清除 |

第三条与第七条是这个架构的支点：**状态在 CR 里**，**负载在构建时固定**。前者让所有组件可重启，后者让创建路径极短。

## 模板：两层，一个制品

模板存在于两个地方，这是刻意的：

- **服务端持久化元数据**（SQLite/PostgreSQL）是模板定义与租户归属的事实来源。模板是租户私有的，通过同一个服务器管理，用同一套认证与租户逻辑。
- **`SandboxTemplate` CR** 驱动 fast-sandbox 侧的实际构建。服务器持久化定义并创建 CR，平台的 builder 异步把它变成 golden image。

构建产物不是简单的镜像复制，而是一次转换：普通 OCI 镜像 + OpenSandbox 的 guest 接线，编译成不可变的 golden image。

- **guest 接线被注入**：init 路径（PID 1，携带就绪标记与心跳）与 execd 运行时文件总是来自模板，所以由它构建出的每个沙箱都「天然 execd-ready」。
- **业务 entrypoint 与 env 被烘进去**：空 entrypoint 默认成 keep-alive，env 字面注入（没有运行时间接层）。
- **运行时形状被声明**：模板指明 Firecracker 的 guest kernel 与参考机型，以及「什么算就绪」。
- **输出是内容寻址的**：构建把制品发布到 store，并在 CR status 里记录 manifest 引用与 digest。构建不可变、可验证，绝不原地修改。

于是创建请求里不能带 `entrypoint`、`env`、`volumes`、资源——它们属于模板。服务器适配层会直接拒绝这些字段（第 5 篇已提过）。

模板的第二个性质：**更新会产生新 digest，而已运行的沙箱继续用当初被接纳的版本**。新的创建才用新构建。这就是内容寻址 + 构建不可变带来的行为。

## 创建热路径

```text
POST /v1/sandboxes {templateId, timeout, metadata, networkPolicy}
  → 服务器持久化/校验，向 FastPath 提交 intent（gRPC）
  → FastPath 在内存候选里排序：镜像缓存亲和 → 归一化负载 → 稳定哈希
  → 在选中的 Fastlet 上原子接纳：分配网络槽位、写 CR（意图 + 初始策略 + pool 引用）
  → runtime agent 恢复 vmstate.snap，或启动 VM
  → 沙箱 Running；客户端重新解析 endpoint
```

三个细节值得停一下：

**网络槽位先于运行时存在。** netns、veth、masquerade 在任何创建请求之前就为沙箱预置好了，attachment 身份与策略绑定随接纳一起下发。所以沙箱永远不会「先能通网、后被策略覆盖」。

**CR 先写、且写全。** 意图、初始策略、pool 引用在运行时启动之前就持久化。被任何失败打断的接纳都能被 reconcile 补齐，不会漏掉半个沙箱。

**entrypoint 作为 task 下发**，而不是烘进 Pod。这让一个预热 Fastlet 能服务任意模板。

## 网络：签名路由与共享 egress

Fast Sandbox 的网络在入向和出向各有一个适配器，都在本仓库里。

**入向**由 ingress 的 `FastSandboxProvider` 承担：

- 一条路由是签名的 `f1.` scope，绑定租户命名空间、沙箱 ID、端口，用 `opensandbox-fsb-route-v1` 方案签名，ingress 共享服务器的签名密钥环。
- 客户端必须原样保留返回的路由与头——**scope 就是凭据**。
- 服务器签发路由时**不调用 FastPath、不等就绪**：签名是本地操作，所以签发与沙箱状态解耦，endpoint 发现不构成存在性证明。
- 请求到达时 ingress 才调 `ResolveEndpoint`。失败语义是契约的一部分：`FailedPrecondition`（含路由尚未发布）以 `503` + `Retry-After` 出现，**绝不能被报成「沙箱不存在」**——ingress 不能泄漏「没就绪」与「不存在」的差别。
- 通配 host 模式**故意不支持**：命名空间、沙箱 ID、MAC 塞不进一个 DNS label。
- 两种访问模式：`direct-fastlet-proxy`（默认，ingress 直连 Fastlet Pod IP，低延迟）与 `central-proxy`（多一跳，不要求 Pod IP 可达）。

**出向**是这一篇最值得抄的设计。Fastlet Pod 里放着很多沙箱，per-sandbox sidecar 不适用，所以 egress 组件以 `OPENSANDBOX_EGRESS_PROFILE=fast-sandbox` 运行：**每个 Fastlet Pod 一个 egress 进程，服务 N 个沙箱，每个沙箱是一个 subject**。

subject 有自己的状态机：

```text
absent ──SET_BINDING──► denying ──data-plane-ready──► active
   ▲                        │                            │
   └──────REMOVE_BINDING────┴────────────────────────────┘
```

从注册开始就是 `denying`，只有数据面确认就绪才 `active`。文档的措辞很准：**策略可以迟到，绝不能提前放行**。

策略怎么到 subject？作为数据，走 `sandbox.fast.io` CRD 的 revisioned `actionBindings`；只有凭据是带外的、只在内存里的。执行靠 nftables，**按源 IP 分发**：

- 每个沙箱有一个预置的网络槽位，`attachment.ip` 就是分发键，共享 DNS 代理按 `ip saddr` 选 subject。
- forward 路径用 `meta mark 0x2` 标记允许的目标，master 链丢弃未标记流量。这条路径**故意不发显式 accept**：在 `bridge-nf-call-iptables=1` 的拓扑下，accept 裁决会把帧送回二层而丢失。
- DNS 学到的 IP 有界租约，活跃 TCP 连接有刷新循环续租；UDP/QUIC 只靠 DNS TTL。
- DoT（853）总是丢，DoH-over-443 可选阻断。

这套设计的价值不在 nftables 语法，而在「共享基础设施 + 每 subject 隔离」这个折中：省掉了 N 个 sidecar 的资源，代价是策略执行路径变复杂，必须用 UID/IP/端口三重作用域把管理面与数据面隔开。

## 检查点、暂停、快照

Fast Sandbox 的暂停把运行时状态检查点写进**同一个内容寻址 store**，其中 golden image 也放在那里。一个检查点包含：

| 部分 | 内容 |
|---|---|
| `rootfs` | 沙箱可写根文件系统 |
| `vmstate` | microVM 的设备与 vCPU 状态 |
| `memory` | guest 内存（捕获期间经节点 `/dev/shm` 暂存） |
| `SHA256SUMS` | 整个集合的 digest 清单 |

Sandbox 记录的 `status.runtime.checkpoint` 存 `checkpointID`（由 sandbox UID、请求暂停的 spec generation、`pauseAttempt` 重试纪元派生）、`manifestRef`（`s3://` URI）、digest 与大小。检查点**先写 digest 后写内容**：只有制品集完整落到 store，暂停才被报告为完成。没有记录检查点的 `Paused` 沙箱无法恢复，只能重置。

暂停的第 3 步是**释放 Fastlet 容量**。所以一个 paused 沙箱的算力足迹是零。

恢复的关键性质是**可移植**：在任意被接纳的 Fastlet 上恢复，不一定是原宿主机。这就是为什么路由必须重新解析——恢复会推进 route generation，旧路由不会跟着沙箱跑到新宿主机。

快照是同一套机制的公开形态：`POST /sandboxes/{id}/snapshots` 只接受 `Running` 源，创建确定性命名的 `SandboxSnapshot` 记录 + `sandboxRef.uid` 围栏，然后持久化 `CREATING` 行。文档的说法是「运行时意图永远在一行可读记录后面，在每个副本上」。之后由平台推进 `Pending → Creating → Publishing → Succeeded/Failed`。恢复时 `POST /sandboxes {"snapshotId": ...}` 从 `READY` 快照创建，**沙箱 ID 是新的**——快照是起点，不是延续。

删除快照记录**不删除**已推送的制品。存储保留是运维策略，被有意留在沙箱生命周期之外（第 17 篇会把它和容量规划放在一起）。

## 存储：一个 store，私有缓存，P2P 分发

Fast Sandbox 把存储压缩成一种形状：**沙箱运行或恢复所需的全部东西，都是单一 S3 兼容 store 里的内容寻址制品**。

节点保留**私有状态根**：XFS reflink 支撑的本地缓存，节点之间不共享。这是有意的——快速启动来自本地制品，跨节点共享状态会把每个节点变成别人故障域的一部分。

私有缓存的代价是重复下载，DART 消除它：每节点一个投递守护进程，通过 headless Service 名册互相发现，4 MiB 块级取数走 `cache → peer → origin`，每个块全集群大约只从源站取一次。每节点的块来源计数（cache/peer/origin）让投递路径可观测。

文档明确列了「故意不存」的三样东西，这段很值得读：

- **egress 凭据**只在 egress 进程内存里，绝不写 Secret 卷或磁盘；
- **沙箱的运行时写入**在沙箱自己的切片里，只有显式检查点或快照才捕获——「沙箱的存储是生命周期的一个动作，不是后台同步」；
- **路由与策略状态**在 CRD 里，store 只放字节。

## Firecracker 与节点自检

模板构建 golden image 时就是为 Firecracker 准备的，每个沙箱是 Fastlet Pod 网络命名空间里的一台真 KVM 虚拟机。

宿主要求（在 chart 与环境工具里实际检查的）：

- 宿主内核 ≥ 5.10；
- `/dev/kvm` 存在并透传进节点容器（含 `/sys` 下的 KVM 设备节点），嵌套虚拟化可用即可；`/dev/net/tun` 用于每沙箱 netns 的 TAP；`vhost-vsock` 故意不挂；
- `/dev/shm` 大于容器默认值（快照内存暂存在这里）；
- cgroup v2（v1 宿主直接拒绝）；
- 提高 `fs.inotify.max_user_instances`；
- 节点状态根在 **XFS + reflink** 上：每沙箱 rootfs 是 CoW reflink，setup 会显式探测 reflink，探测失败就拒绝而不是降级；
- 余量：默认要求 10 GiB 空闲磁盘与 2 GiB 空闲内存。

**节点就绪是自助的**：firecracker-runtime DaemonSet 在每个候选节点跑检查、装 Firecracker 资产（默认 `v1.16.1`），通过后才给节点打标签（`sandbox.fast.io/kvm=true` 等）并设 `FirecrackerReady` 条件。池只把 Fastlet 放到打过标签的节点上，**运维不用手工打标签**。

网络模型里有两个踩过的坑，文档写得很具体：

- **每 VM 独立网关地址。** 共用一个网关在 `arp_ignore=1` 下会坏：第二台 VM 的 ARP 没人应答，流量出不了 guest。所以每 VM 一对 veth 与独立网关 IP，egress 按 attachment 自动分发。
- **guest 流量绕过容器 netfilter。** 跨 VM netns 的流量从 Pod 视角是二层桥流量，所以权威执行层是 pod-netns 的 forward 链，不是容器 OUTPUT 路径。

生命周期顺序是固定的：建 VM netns → `SET_BINDING`（deny-first）→ `data-plane-ready` → 沙箱流量在自身策略下流动。

## 性能数怎么读

文档给的 ACK 两节点实测（2026-10-08，Firecracker 1.16.1，模板制品已缓存）：

| 指标 | 成功率 | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| SDK 创建，串行基线（100 次） | 100/100 | 64.0 ms | 93.8 ms | 108.3 ms |
| SDK 创建，并发 10 | 100/100 | 102.7 ms | 120.1 ms | 125.4 ms |
| SDK 创建，并发 20 | 51/100 | 136.7 ms | 163.0 ms | 197.0 ms |

读这张表要带三个前提：

1. **SDK 就绪包含整条链**：Python SDK、生命周期服务器、FastPath、ingress gateway、guest `/ping` 200。运行时创建更早结束（同窗口的 runtime create p50 是 33–50 ms）。
2. **并发 20 那一行只统计成功的尝试**。48 次 `SandboxRateLimitException`（HTTP 429 / Fastlet `CapacityRejected`）和 1 次就绪超时被排除在外。文档自己写明：这次运行**不能**证明 20 路并发接纳可靠，也不能给出可持续的 RPS。
3. **同节点恢复仍然慢**。运行时恢复是几十毫秒（resume runtime creation 中位 35.49 ms），但 SDK 恢复中位 **62.66 秒**。文档明确指出：大部分时间花在 endpoint/guest 可用性与客户端等待上，无法用复制 2 GiB rootfs 或跨节点取检查点解释；这个差距尚未定位。

这三条一起说明了一件事：**预热能把「运行时创建」压到几十毫秒，但不能自动把「端到端就绪」压到同量级**。第 18 篇会把这条当作「性能优化要优化在哪一层」的例子。

## 高可用：单控制面是设计立场

Fast Sandbox 的控制器（reconciler + FastPath）**单副本、无 leader election**。文档把这个决定写成了立场而不是遗漏：

- 变更快，正是因为 FastPath 可以把放置与候选状态放在内存里；复制控制面就得对这份状态做共识。
- 没有持久状态依赖那份内存。崩掉的只是进行中的 reconcile，CR 会重放。
- 因此**沙箱的可用性与控制面的可用性解耦**：控制面重启时，运行中的沙箱继续跑，流量继续流。

恢复统一用「提交意图 + 观察持久记录收敛」，不用 lease：

| 失败 | 影响 | 恢复 |
|---|---|---|
| 控制面重启 | 短暂失去变更能力，运行中沙箱不受影响 | CR 驱动重放意图 |
| Fastlet Pod 丢失 | 该 Fastlet 上的沙箱中断 | 按 `failurePolicy` 在 `recoveryTimeoutSeconds`（默认 60 秒）后处理，默认人工 |
| 服务器重启 | 沙箱无影响，未完成的操作从持久记录继续 | 同一套观察-收敛 |
| 制品 store 故障 | 新启动降级为直接拉取，运行中沙箱不受影响 | store 恢复后 DART 继续服务 |

## 本章要点

- Fast Sandbox 是 Kubernetes 模式下的组合分支，通过 `templateId` 与 `fsb-` 前缀路由；控制协议是 gRPC 到 FastPath，控制面之外用 JSON over HTTP。
- 模板两层：数据库里是定义与租户归属的事实来源，`SandboxTemplate` CR 驱动构建；构建产物是含 execd 的内容寻址 golden image。
- 创建走「内存放置 + 原子接纳」，网络槽位与 CR 都先于运行时存在。
- 入向是签名的 `f1.` scope，签发与就绪解耦；出向是每 Fastlet 一个共享 egress，按 subject 分治、按源 IP 分发，fail-closed。
- 暂停写 rootfs+vmstate+memory 检查点并释放 Fastlet；恢复可能换宿主机，所以必须重新解析 endpoint。
- 存储是一个内容寻址 store + 节点私有 reflink 缓存 + DART P2P。
- 控制面单副本是立场；沙箱可用性与控制面可用性解耦。
- 64 ms 的创建与 62 秒的恢复并存，因为两者度量的完成边界不同。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/fast_sandbox/service.py` | 适配层：创建、暂停/恢复、端点 |
| `docs/architecture/fast-sandbox/index.md` | 九个组件与设计原则 |
| `docs/architecture/fast-sandbox/scheduling.md` | 池、Fastlet、创建热路径、失败策略 |
| `docs/architecture/fast-sandbox/networking.md` | 签名 scope、解析语义、subject 生命周期 |
| `docs/architecture/fast-sandbox/checkpoints.md` | 检查点内容、暂停/恢复、快照发布 |
| `docs/architecture/fast-sandbox/storage.md` | store 布局、DART、节点状态根 |
| `docs/architecture/fast-sandbox/firecracker.md` | 宿主要求、每 VM 网关、netfilter 边界 |
| `docs/architecture/fast-sandbox/performance.md` | 实测数字与它们的适用条件 |
