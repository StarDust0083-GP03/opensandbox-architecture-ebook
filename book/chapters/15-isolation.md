# 隔离与加固：从 runc 到 microVM

第 1 篇把「隔离必须是运行时的性质」列为第一条需求。这一篇兑现它，并且要说清一件事：**隔离不是一个开关，是一个分层结构，每层能挡的东西不同。**

## 四层，各自挡什么

execd 文档列的四层结构是全书里最清晰的隔离表述：

| 层 | 内容 | 挡什么 | 谁开启 |
|---|---|---|---|
| 沙箱边界 | 资源限制、身份、隔离运行时（runc/gVisor/Kata/Firecracker） | 容器逃逸、越权访问宿主 | 创建时选定 |
| 加固地板 | 原生 launcher：drop capabilities、`no_new_privs`、syscall 拒绝列表、Landlock 写限制 | 沙箱内部的提权与持久化 | 运维开启 |
| 隔离会话 | per-execution bubblewrap 命名空间 | 同一沙箱内任务之间的相互影响 | 每次执行 |
| eBPF 审计 | 记录 exec / connect / 权限变更 | 事后取证（不阻断） | 单独 `execd-ebpf` 构建 |

关键设计不是这四层的存在，而是**每层都会报告自己的状态**：active、degraded 或 unsupported。客户端能验证实际生效的是哪几层，而不是相信宣传。

第 9 篇提过的那条边界要重复一次：execd 的**生命周期钩子是平台配置的可信代码**，在沙箱内运行。文档原话是「它们是便利，不是安全控制」。任何把初始化工作当安全边界的方案都会在这一句上翻车。

## 安全运行时：一次配置，全局生效

隔离运行时由服务端配置，`[secure_runtime] type` 取 `""`（runc，默认）、`gvisor`、`kata`、`firecracker`。文档给的开销对照：

| 运行时 | 隔离机制 | 启动开销 | 内存开销 |
|---|---|---|---|
| runc（默认） | 进程级 cgroup | ~0 ms | 极小 |
| gVisor | 用户态内核（syscall 拦截） | ~10–50 ms | ~50 MB |
| Kata（QEMU） | QEMU 虚拟机 | ~500 ms | ~20–50 MB |
| Kata（Firecracker） | microVM | ~125 ms | ~5 MB |
| Kata（CLH） | Cloud Hypervisor | ~200 ms | ~10–20 MB |

配置翻译成后端参数，由 `runtime_resolver.py` 完成：

```python
DEFAULT_DOCKER_RUNTIMES = {"gvisor": "runsc", "kata": "kata-runtime"}
DEFAULT_K8S_RUNTIME_CLASSES = {"gvisor": "gvisor", "kata": "kata-qemu", "firecracker": "kata-fc"}
```

Docker 上是一条 OCI runtime 名字，Kubernetes 上是一个 `RuntimeClass`。调用方完全看不到这个差异——这正是第 5 篇「运行时中立」的含义。

启动时会校验声明的运行时可用（`validate_secure_runtime_on_startup`）。不可用就启动失败，理由还是第 4 篇那条：隔离配错时，单个请求看不出来。

## 兼容性矩阵：选型时真正要看的东西

`secure-container` 文档给了一张很实用的表，它比「启动开销」更能决定选型：

| 特性 | runc | gVisor | Kata(QEMU) | Kata(CLH) | Kata(FC) |
|---|---|---|---|---|---|
| syscall 兼容性 | 完整 | 部分 | 完整 | 完整 | 受限 |
| GPU | 是 | 是\* | 是 | 是 | 否 |
| IPv6 | 是 | 是 | 是 | 是 | 是 |
| 特权模式 | 是 | 是 | 是 | 是 | 否 |
| Docker 卷 | 是 | 是 | 是 | 是 | 是 |
| systemd | 是 | 是\* | 是 | 是 | 否 |

`*` 表示需要额外配置。

这张表解释了为什么没有「最佳运行时」：Firecracker 密度最高、开销最低，但 syscall 受限、没有 GPU、不支持特权模式与 systemd。要跑一个需要 systemd 或 GPU 的镜像，就不能用它。

## 跨组件的硬性不兼容

这是本章最重要的部分：几条隔离选择会直接让某些功能失效，而这些约束分散在不同的文档里。

**gVisor × egress。** egress 的两条重定向路径（iptables 与原生 nftables）都需要 gVisor 不支持的 nftables 特性。文档的结论是「两者都不行」：要安全运行时 + 出向策略，用 Kata。

**gVisor × rootfs 快照。** `docs/api/implementation-compatibility.md` 写明：Kubernetes 的 rootfs 快照提交器**拒绝 gVisor RuntimeClass**。也就是说，选择 gVisor 会同时失去「默认出向策略」和「暂停/恢复」两项能力。

**egress × mesh 注入。** 透明 mesh sidecar（Istio/Envoy）与 egress 会在同一命名空间里争抢出向改写。文档给了三条出路：把沙箱 Pod 排除在注入外；保留注入但不启用 egress 的凭据与策略功能；把出向策略移到沙箱 Pod 之外的平台机制。

**Fast Sandbox × guest netfilter。** 跨 VM netns 的流量从 Pod 视角是二层桥流量，所以权威执行层是 pod-netns 的 forward 链，不是容器 OUTPUT 路径。第 8 篇讲过这条，它解释了一个反直觉的现象：**guest 自己的 iptables 不是执行点**。

**Firecracker × 宿主能力。** 需要 `/dev/kvm`、`/dev/net/tun`、cgroup v2、XFS reflink、足够大的 `/dev/shm`，以及 10 GiB 磁盘 / 2 GiB 内存的余量。缺任何一项，节点不会被标记，也不会收到 microVM 沙箱。

把这些放在一起看，选型其实是一张能力取舍表，而不是一个安全等级排序：

```text
要 GPU / systemd / 特权模式        → Kata(QEMU) 或 runc
要最高密度 + 最低开销               → Firecracker（放弃 GPU 与 systemd）
要 syscall 兼容 + 低开销           → gVisor（放弃 egress 包级策略与 rootfs 快照）
要一切功能都能用                    → runc（放弃强隔离）
```

## 加固地板：默认关闭的正确性

加固地板指的是 execd 的原生 launcher：每次启动用户进程时 drop capabilities、设 `no_new_privs`、装 syscall 拒绝列表、用 Landlock 限制文件系统写。

它默认是**关闭**的（由运维开启）。这个默认值我认为是对的，理由是顺序问题：加固地板会改变进程能做的事，而用户镜像的假设可能是「我有完整 syscall」。把它默认打开，会让一大批现有镜像在运行时报奇怪的错误——而这些错误会被误诊为平台 bug。

正确的默认是「隔离边界先到位（运行时），加固地板按需开启（策略）」，而不是反过来的。这也解释了为什么文档强调「每层报告自己的状态」：当加固地板开启后某个镜像坏了，你要能立刻看出是哪一层变的。

## 隔离能挡什么，不能挡什么

把这一章收紧，需要一份诚实的威胁模型。根据前面的材料，可以列出：

**能挡住的**：

- 容器逃逸到宿主（取决于运行时强度）；
- 沙箱之间互相访问（命名空间 + 私有 CIDR 的同级隔离规则）；
- 从内部关闭或绕过网络策略（沙箱没有 `NET_ADMIN`，规则在 sidecar/共享 egress 里）；
- 拿到真实凭据（Vault 把秘密留在沙箱之外）；
- 同一沙箱内不同任务互相影响（隔离会话的 per-execution 命名空间）。

**挡不住的**：

- **通过被允许的出向目标泄露数据**。策略允许 `pypi.org`，那么沙箱就能向 `pypi.org` 发数据。域名白名单是目的地控制，不是数据流控制。
- **提示注入导致的沙箱内恶意行为**。隔离限制的是「能碰到什么」，不是「会做什么」。一个被注入的 Agent 可以合法地删掉自己的工作区。
- **跨租户共享命名空间时的软隔离绕过**（如果部署选择了共享命名空间）。
- **eBPF 审计的缺失**（默认镜像不含它）。
- **生命周期钩子里的错误**（它们是可信代码，出错就是沙箱内出错）。

这份清单比「我们提供四层防护」有用得多。它也是第 18 篇讨论「这套设计放弃了什么」的输入。

## 本章要点

- 四层防护：沙箱边界、加固地板、隔离会话、eBPF 审计，每层自报 active/degraded/unsupported。
- 隔离运行时由服务端配置，翻译成 Docker OCI runtime 或 K8s RuntimeClass，调用方无感；不可用时启动失败。
- 兼容性矩阵决定选型：Firecracker 密度最高但 syscall 受限、无 GPU、不支持 systemd/特权。
- 硬性不兼容要记住：gVisor 不支持 egress 包级策略与 rootfs 快照；egress 与 mesh 注入冲突；Fast Sandbox 的执行点是 pod-netns forward 链。
- 加固地板默认关闭，因为「先边界后策略」的顺序错误会制造误诊。
- 隔离挡住宿主逃逸与内部绕过，挡不住「通过被允许的出向目标泄露数据」与提示注入导致的沙箱内破坏。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/runtime_resolver.py` | 安全运行时到后端参数的翻译与校验 |
| `components/execd/pkg/runtime/` | 加固启动器、capabilities、Landlock |
| `components/execd/pkg/isolation/` | bubblewrap 隔离会话 |
| `docs/guides/secure-container.md` | 开销、配置、兼容性矩阵 |
| `docs/guides/network-isolation.md` | 出向策略的设计与安全默认值 |
| `oseps/0004-secure-container-runtime.md` | 设计提案 |
