# 运行时路由：一个接口装下三种后端

第 4 篇说「API 层不碰运行时」。那么运行时到底是什么形状？答案是 `SandboxService` 这个抽象类，它定义了 13 个方法。

## 接口全貌

```text
create_sandbox          list_sandboxes         get_sandbox
delete_sandbox          pause_sandbox          resume_sandbox
renew_expiration        patch_sandbox_metadata
get_endpoint
get_sandbox_log_diagnostics    get_sandbox_event_diagnostics
get_sandbox_logs / get_sandbox_inspect / get_sandbox_events
```

前八个是生命周期，第九个是访问，后四个是诊断。这个划分本身就说明了一件事：**接口是按「调用方能做的事」组织的，不是按后端实现组织的**。诊断能进入接口，是因为运维和 AI 排障需要它，而不是因为 Docker 恰好有 `docker logs`。

接口里还有两个非抽象成员值得注意。一个是 `set_tenant_provider`，默认空实现（非 Kubernetes 后端不需要）；另一个是 `close`，用于释放后台 reader 和连接。两者都是「可选能力」——这暗示了抽象类的一个现实：**接口能统一签名，统一不了依赖**。

## 工厂：四行决定三个后端

```python
implementations = {"docker": DockerSandboxService, "kubernetes": KubernetesSandboxService}
if selected_type not in implementations:
    raise ValueError(...)
if selected_type == "kubernetes":
    implementation = KubernetesSandboxService(config=active_config)
    return CompositeSandboxService(implementation,
        FastSandboxService(active_config, k8s_client=implementation.k8s_client))
return implementations[selected_type](config=active_config)
```

三个细节：

1. `runtime.type` 的取值集合只有两个，Fast Sandbox 不在这里注册。
2. `FastSandboxService` 复用 `KubernetesSandboxService` 已经建好的 `k8s_client`，不重复建连。
3. 不认识的类型直接抛错，不在启动后降级。

第 3 点是第 4 篇「启动即失败」原则在小处的复现：配置写错了，进程根本起不来。

## 组合路由器：前缀即分发

`CompositeSandboxService` 把两个实现合成一个，规则只有两条：

```python
def _backend(self, sandbox_id):
    return self._fsb if sandbox_id.startswith("fsb-") else self._kubernetes

async def create_sandbox(self, request):
    if (request.template_id or "").strip():
        return await self._fsb.create_sandbox(request)
    if (request.snapshot_id or "").strip() and request.resolved_snapshot_backend == "fsb":
        return await self._fsb.create_sandbox(request)
    return await self._kubernetes.create_sandbox(request)
```

创建时看请求来源，之后看 ID 前缀。文档给的理由是「没有新的公开 API，也没有沙箱到后端的注册表」。

这个设计的优点很实在：**零状态**。不需要一张「sandbox → backend」的表，也就不需要维护它的一致性、不需要在切换后端时迁移。第 17 篇讨论故障恢复时会看到它的好处：控制面崩溃重启后，路由信息仍然在 ID 里。

缺点也同样实在：

- **前缀是硬编码的知识**。任何知道 `fsb-` 含义的代码都成了隐式耦合点，包括 SDK（它通过 `OPEN-SANDBOX-ORIGIN` 头而非前缀来判断，但行为要一致）。
- **前缀不能表达第三个后端**。如果将来出现第四种运行时，要么再挑一个前缀，要么改成真正的注册表。
- **前缀由谁生成**必须严格约束。上游的做法是每个后端自己生成 ID，模板后端加 `fsb-` 前缀；一旦某个后端漏了前缀，路由会静默走错分支——这不是崩溃，是错误的行为，更难查。

我倾向于把它看成一次有意识的取舍：在只有两个分支的时候，前缀分发比注册表省下的复杂度是真实收益；当分支增长到三个，这笔账要重算。

## 快照的跨后端路由

创建请求里还有一个不显眼但重要的分支：`snapshotId` 走哪个后端？

答案不是「根据当前 runtime 猜」，而是**问快照记录**。`CreateSandboxRequest` 里有一个私有属性 `_resolved_snapshot_backend`，在路由之前由 `resolve_sandbox_image_from_request` 从快照行里读出 `restore_config.backend`。这样「这个快照是谁做的」由数据决定，而不是由当前部署的配置决定。

这条规则的必要性在于：快照是持久制品，可能产生于另一个后端或另一次部署。Docker 做的快照是本地 OCI 镜像，Kubernetes 做的是推送到 registry 的镜像，Fast Sandbox 做的是内容寻址的 artifact set。三者的恢复路径完全不同，靠猜一定会猜错。

## 13 个方法在三种后端上的样子

接口统一，实现各不相同。这张表是本章的核心：

| 方法 | Docker | Kubernetes 容器 | Fast Sandbox |
|---|---|---|---|
| `create` | 拉镜像、建容器、注入 execd、分配端口 | 建 `BatchSandbox` 或从 Pool 分配、等 Running | 经 FastPath 提交 intent，CR 收敛 |
| `list` | 按标签查容器 | 合并两个 provider 的对象列表 | 读 `sandbox.fast.io` CR |
| `get` | inspect 容器 + 标签 | 读工作负载状态 + 注解 | 读 CR，状态由观测得出 |
| `delete` | 停容器、清端口、删卷 | 删除 CR，等待 Pod 消失 | 提交删除，janitor 兜底 |
| `pause` | 容器级 pause（先冻结主容器再冻结 sidecar） | 提交 rootfs 镜像、释放 Pod | 检查点写入 artifact store，释放 Fastlet |
| `resume` | 容器级 unpause（先解 sidecar） | 改模板为新镜像、重建 Pod | 在任意 Fastlet 恢复检查点 |
| `renew` | 改定时器 | 改 `expireTime` | 改 CR 的 `expireTime` |
| `endpoint` | 宿主端口 + `/proxy/{port}` | ingress 头路由或 pod IP | 签名 `f1.` scope |
| 诊断 | 容器日志/事件 | 工作负载日志/事件 | 只支持 runtime 事件（日志未实现） |

最后一行是诚实清单：Fast Sandbox 的诊断能力比另外两个弱，文档写明了「log collection is not implemented there」。

## 不支持的功能怎么失败

接口统一之后，最危险的情况是「方法存在但后端不支持」。上游的处理是**按操作返回明确错误**，而不是编造成功：

| 场景 | 状态码 |
|---|---|
| Docker 上使用模板管理 | `501` |
| 不支持的运行时上做快照预检 | `409` |
| 隔离会话的 diff/commit 未实现 | `503` |
| Fast Sandbox 上带 `credentialProxy.enabled` 或卷挂载创建 | 请求被拒绝（adapter 明确拒绝） |

文档承认「没有统一的不支持状态码」，并把这当成需要显式文档化的现状。我认为这是对的选择：统一成一个状态码会让调用方无法区分「这个后端不支持」「你没权限」「暂时不可用」——而这三种情况应该触发完全不同的客户端行为（换后端 / 换凭据 / 重试）。

## 后端内部的适配层

抽象接口不覆盖的差异，被压进各后端自己的目录里。几个典型例子：

**Docker 的 Windows 特化。** `services/docker/windows_profile.py` 单独处理 Windows 容器的差异：环境变量名大小写不敏感、批处理文件需要 shell 语法、文件权限语义不同。这些没有污染接口。

**端口的分配与回收。** `services/docker/port_allocator.py` 负责在非 host 网络模式下分配宿主端口。这是 Docker 独有的概念（Pod 没有「宿主端口映射」），所以它只存在于 Docker 目录里。

**安全运行时的解析。** `services/runtime_resolver.py` 把服务端配置翻译成后端参数：

```python
DEFAULT_DOCKER_RUNTIMES = {"gvisor": "runsc", "kata": "kata-runtime"}
DEFAULT_K8S_RUNTIME_CLASSES = {"gvisor": "gvisor", "kata": "kata-qemu", "firecracker": "kata-fc"}
```

同一份 `[secure_runtime] type = "kata"` 配置，在 Docker 上变成 `--runtime kata-runtime`，在 Kubernetes 上变成 `runtimeClassName: kata-qemu`。调用方完全看不到这个差异——它连字段都没有。

**Kubernetes 的工作负载 provider。** `kubernetes.workload_provider` 可取 `batchsandbox`（默认，自有 CRD）或 `agent-sandbox`（`kubernetes-sigs/agent-sandbox`）。这是「同一后端下的两种实现」，比「三个后端」更细一层。上游没有把它做成两个 `runtime.type`，理由大概是：客户端不该关心集群里装的是哪套 CRD。

## 抽象够不够

用一次思想实验检验这个接口：如果要接第四种后端（比如本地 microVM 或者一台远程 SSH 主机），需要改什么？

- 实现 13 个方法；
- 提供一个 ID 生成规则，最好带前缀；
- 在 `factory.py` 注册，或者扩展 `Composite` 的分支；
- 在 `docs/api/implementation-compatibility.md` 里写清哪些能力不支持。

不需要改 API、schema、SDK。这说明抽象的形状是对的。

但也有一条暗线：**endpoint 的语义**。接口只要求返回一个 `Endpoint`，没有约束它「必须直连」「必须支持 WebSocket」「必须能带自定义头」。这些差异被写在了文档里，而不是类型里。类型系统无法表达「这个后端返回的 endpoint 不支持任意 TCP 转发」——第 10 篇会看到这条限制的实际后果。

## 本章要点

- `SandboxService` 有 13 个方法：8 个生命周期、1 个访问、4 个诊断，按调用方能力而非后端实现组织。
- 工厂只认两个 `runtime.type`，Fast Sandbox 是 Kubernetes 模式下的组合分支。
- 前缀分发换来零状态路由，代价是隐式耦合和「不能表达第三个后端」。
- 快照创建走哪个后端由快照记录决定，不由当前配置决定。
- 不支持的功能按操作返回 `501`/`409`/`503`，不编造成功。
- 后端差异被压进各后端目录，接口只保留跨后端的形状。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/sandbox_service.py` | 13 个抽象方法与两个可选成员 |
| `server/opensandbox_server/services/factory.py` | 工厂与组合 |
| `server/opensandbox_server/services/composite_service.py` | 前缀路由、创建分流 |
| `server/opensandbox_server/services/runtime_resolver.py` | 安全运行时的后端翻译与启动校验 |
| `server/opensandbox_server/services/snapshot_restore.py` | 快照到后端的解析 |
| `docs/api/implementation-compatibility.md` | 后端能力差异的权威表述 |
