# 技术方案：一条契约、三种后端、两个平面

需求确定之后，方案的第一件事是划分边界。OpenSandbox 的划法可以用一句话概括：**对外一条契约，对内三种后端，横向切两个平面**。这一篇讲这三句话各自意味着什么，以及为什么不是别的划法。

## 一条契约

`specs/` 目录下有四份 OpenAPI 文件，它们是公开契约的唯一来源：

| 文件 | 大小 | 服务的对象 |
|---|---:|---|
| `sandbox-lifecycle.yml` | 约 120 KB | 控制面：创建、查询、删除、续期、endpoint、暂停/恢复、快照、模板、网络策略 |
| `execd-api.yaml` | 约 119 KB | 数据面：命令、文件、目录、代码上下文、指标、隔离会话 |
| `egress-api.yaml` | 约 26 KB | 出向策略与凭证保管 |
| `diagnostic-api.yml` | 约 16 KB | 诊断日志/事件描述符 |

四条契约分给三类进程：生命周期服务器实现第一、第四条；`execd` 实现第二条；`egress` 实现第三条。它们之间没有共享的 RPC，只有 HTTP 和「客户端先解析 endpoint，再直连」的约定。

这个选择有代价：客户端要理解三个不同的 base URL（默认分别是 8080、44772、18080），还要知道哪些操作在控制面、哪些在数据面。收益是边界非常硬——控制面的故障不会让正在运行的命令中断，反过来也一样。第 17 篇会把这条边界当作可用性讨论的基础。

## 三种后端

`runtime.type` 只有两个合法值：`docker` 和 `kubernetes`。第三种后端 Fast Sandbox 不是新的 `runtime.type`，而是 Kubernetes 模式下的一个组合分支。

```text
runtime.type = "docker"
  └── DockerSandboxService         直接操作 Docker daemon

runtime.type = "kubernetes"
  └── CompositeSandboxService
        ├── KubernetesSandboxService   容器负载：BatchSandbox 或 agent-sandbox
        └── FastSandboxService         模板负载：microVM，经 FastPath 控制
```

路由规则写在 `composite_service.py` 里，短到可以整段引用：

```python
def _backend(self, sandbox_id: str) -> KubernetesSandboxService | FastSandboxService:
    return self._fsb if sandbox_id.startswith("fsb-") else self._kubernetes

async def create_sandbox(self, request: CreateSandboxRequest) -> CreateSandboxResponse:
    if (request.template_id or "").strip():
        return await self._fsb.create_sandbox(request)
    if (request.snapshot_id or "").strip() and request.resolved_snapshot_backend == "fsb":
        return await self._fsb.create_sandbox(request)
    return await self._kubernetes.create_sandbox(request)
```

创建时看请求（有 `templateId` 走模板），之后看 ID 前缀（`fsb-` 走模板）。**没有注册表，没有额外的数据库表**，前缀就是分发器。这个做法在第 8 篇还会再讨论一次，因为它同时是这套设计最省事和最脆的地方。

## 两个平面

架构文档把交互路径分成三条：生命周期管理（橙）、执行与服务访问（蓝）、出向控制（绿）。落到进程上就是两个平面：

**控制面**只做四件事：认证、校验、持久化自己拥有的记录、把动作委派给运行时。

**数据面**做剩下的全部：命令执行、文件读写、PTY、Jupyter、指标，以及出向流量的 DNS 与包过滤。

中间那条线可以写成一条规则：**控制面不碰沙箱内部，数据面不碰沙箱的帐**。

这条规则解释了三个容易困惑的现象：

- 为什么服务器不保存沙箱列表？因为容器、Pod、CR 才是事实来源（`docs/architecture/control-plane/server.md` 的原话是「Sandboxes are not rows in the server's database」）。
- 为什么服务器要用代理转发流量？那不是主路径，是当直连和网关都不通时的兼容退路。
- 为什么 `execd` 需要自己的令牌？因为控制面的 API key 只授权「管理沙箱」，不授权「进入沙箱」。

## 一张图看全

图 1 分开画管理请求、入向访问、出向控制和旁路观测。运行时适配只列出选择，不把所有组件假装成一条串行流水线。

{{diagram:architecture|图 1：OpenSandbox 组件与交互关系。管理请求进入生命周期服务器；执行流量经解析后的路径到达沙箱；egress 管出向，观测走旁路。容器使用 sidecar，Fast Sandbox 使用每 Fastlet 共享的 egress 进程。}}

同一张图换一个角度看：一次创建请求在时间轴上的落点，以及每一跳对应的章节。

{{diagram:request-flow|图 2：Kubernetes BatchSandbox 池化成功路径（资源 watch 与任务执行器细节作逻辑归并）。创建响应不等于应用就绪；拿到 endpoint 与请求头后仍需健康检查。容量耗尽返回 429 的请求不进入后续执行步骤。}}

两条容易被这张图纠正的直觉：

- **创建是异步的，但 K8s 容器后端会等到 `Running`**（第 7 篇），所以「轮询到就绪」在多数场景只剩一次确认。
- **容器后端中 execd 与 egress 共享网络命名空间，只有 egress 持有所需的 `NET_ADMIN`**；Fast Sandbox 的共享 egress 在 microVM guest 外，不能套用同一进程布局。

## 运行时中立，执行具体

生命周期 API 里全是跨运行时的概念：`resourceLimits`、`volumes`、`networkPolicy`、`platform`、`secureAccess`、`metadata`、`extensions`。不同后端把它们翻译成不同的东西：

| 概念 | Docker | Kubernetes 容器 | Fast Sandbox |
|---|---|---|---|
| 算力单位 | 容器 | Pod | microVM |
| 卷 | 绑定挂载 / 命名卷 | PVC / hostPath | 不支持 |
| 出向策略 | 每沙箱 sidecar | Pod 内 sidecar | 每 Fastlet 一个共享进程 |
| 暂停 | 容器 pause | 提交 rootfs 镜像并重建 | 检查点 + 释放 Fastlet |
| endpoint | 宿主端口 + `/proxy/` | ingress 头路由 | 签名路由 scope |

这张表的每一行都在文档里能查到。它同时说明了一件事：**「中立」只到 API 形状为止**。同一个 `networkPolicy` 字段，在三种后端上能做的事情并不一样，所以第 3 篇要讲「契约里有一条操作，不等于每个后端都支持它」。

## 六条设计原则

上游在架构文档末尾列了设计原则，我认为其中四条真正约束了代码形状，值得逐条落到代码上：

**Protocol First。** 公开行为从 `specs/` 开始。这条原则最直接的体现是：服务器代码里的 `API_CONTRACT_VERSION` 与 `specs/sandbox-lifecycle.yml` 的 `info.version` 对齐，而包版本另走 `GET /version`。契约版本和发布版本是两个东西。

**Control Plane vs Data Plane。** 上面已讲。

**Secure Defaults with Explicit Escape Hatches。** 默认要有 API key；非交互环境下没 key 直接拒绝启动，除非显式设置 `OPENSANDBOX_INSECURE_SERVER=YES`。多租户开启时，启动阶段就要校验所有租户命名空间可达，否则不接流量。这条「启动即失败」比「请求时失败」强的理由是：隔离配置错了，单看某个请求是看不出来的。

**Observable Failures。** 沙箱状态不只有 `state`，还有 `reason`、`message` 和状态迁移时间。`execd` 有 metrics，服务器有 diagnostics，ingress/egress 有连接分类与拒绝计数。第 16 篇会讲这套可观测性的形状。

## 仓库地图

读代码前先看清目录职责，能省很多时间：

| 目录 | 职责 | 语言 |
|---|---|---|
| `server/` | 生命周期控制面（FastAPI） | Python |
| `components/execd/` | 沙箱内执行守护进程 | Go |
| `components/egress/` | 出向策略、凭证代理、nftables | Go + Python（mitmproxy 插件） |
| `components/ingress/` | Kubernetes 入向反向代理 | Go |
| `components/nodeagent/` | 节点级日志/系统调用采集 | Go |
| `kubernetes/` | CRD 与三个 reconciler | Go |
| `sdks/sandbox/*` | 五种语言的沙箱 SDK | Py/TS/Kotlin/Go/C# |
| `sdks/code-interpreter/*` | 代码解释器 SDK | Py/TS/Kotlin |
| `cli/` | `osb` 命令行 | Python |
| `manifests/charts/` | Helm chart（base/controller/server/…） | YAML |
| `specs/` | OpenAPI 契约 | YAML |

按代码量粗看（`go/py/ts/java/cs` 计）：`sdks/sandbox` 约 20 万行，`server` 约 8.4 万行，`components/egress` 约 9.6 万行，`components/execd` 约 6.3 万行，`kubernetes` 是中等规模但密度最高的一块。SDK 体量大是因为五种语言各自生成加手写适配，不是因为逻辑复杂。

## 为什么不是别的划法

有三个看起来更简单的方案，值得说明它们为什么不在这里：

**方案 A：把执行 API 合进生命周期服务器。** 这样只有一个 base URL，客户端最简单。代价是命令的 SSE 流会穿过控制面，控制面升级或重启就会打断正在跑的任务；而且所有沙箱的命令流量都要过服务器，带宽和连接数都成了控制面的问题。上游选了分离，把「流」留在数据面。

**方案 B：把三种后端做成三个独立产品。** 这样每套可以按自己的语义优化。代价是 SDK 要写三份，用户体验碎片化，而且「本地 Docker 起步、上线换 K8s」这条最常见的迁移路径会失效。上游选一个接口 + 组合路由，代价是必须接受「某些功能只有某些后端支持」。

**方案 C：用 Kubernetes 一种后端覆盖全部。** 这样可以少维护一套 Docker 实现。代价是本地开发必须装集群，而 README 的第一句引导就是 `uvx opensandbox-server` + Docker。上游保留 Docker 作为一等公民。

三个方案都不是错的，只是各自把复杂度放在了不同地方。OpenSandbox 把复杂度放在了「运行时适配层」和「文档里的兼容性边界」上——这是最不影响调用方、但最考验维护者的位置。

## 本章要点

- 四份 OpenAPI 契约分给三类进程，控制面与数据面之间没有 RPC。
- `runtime.type` 只有 `docker` 与 `kubernetes`；模板沙箱是 Kubernetes 模式下的组合分支，用 `fsb-` 前缀分发。
- 控制面不碰沙箱内部，数据面不碰帐；因此服务器不保存沙箱列表。
- 运行时中立只到 API 形状，能力差异必须显式声明。
- 三条被放弃的替代方案，各自把复杂度放在了别处。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `docs/architecture/index.md` | 六个 surface、三条交互路径、五条核心流程 |
| `server/opensandbox_server/services/factory.py` | 配置到实现的映射，共 40 行 |
| `server/opensandbox_server/services/composite_service.py` | `fsb-` 前缀路由与创建分流 |
| `specs/` | 四份契约的实际大小与 `info.version` |
| `manifests/charts/opensandbox/` | umbrella chart 如何把组件组合起来 |
