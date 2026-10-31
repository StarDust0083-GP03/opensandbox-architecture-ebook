# 可观测性与诊断：让失败留下证据

沙箱平台有一个特殊的观测难题：**被观测的东西是短命的**。一个沙箱活了 90 秒，跑了一堆命令，然后被删掉。它打印的一切跟着消失。如果这时用户说「Agent 报错了」，你手上什么都没有。

OpenSandbox 为此准备了四条互不重叠的证据链：状态与原因码、请求 ID、指标、以及可选的节点级采集。这一篇按「出问题时你会按什么顺序查」来组织。

## 第一层：状态本身就是证据

最便宜的证据在 API 响应里。沙箱对象带 `state` / `reason` / `message` / `lastTransitionAt`，四者合起来能回答大部分初级问题：是镜像问题、调度问题，还是我自己操作错了。

配套的还有 diagnostic 契约里的**结构化路由上下文**：`GET /sandboxes/{id}/diagnostics/context` 返回运行时后端与后端定义的诊断标识符。

| 后端 | context 内容 |
|---|---|
| Docker | 后端名 `docker` + 容器标识符 |
| Kubernetes | 命名空间/工作负载标识符 + 已分配的 Pod 名（如果有） |
| Fast Sandbox | `fast-sandbox` + 空标识符 map |

第三条的「空 map」不是缺陷，是诚实：Fast Sandbox 的运行时标识不在 Kubernetes 资源里，适配层拿不到。文档还说明 context 不需要 scope、不依赖 execd 就绪，而且 Kubernetes 的 context 可以在 Pod 分配之前查询。

## 第二层：请求 ID 把链路串起来

服务器有 `RequestIdMiddleware`，`X-Request-ID` 是可选请求头——不传就自己生成。日志、指标、诊断都带这个 ID。

这个机制很小，但它是跨组件排障的前提：客户端报了一个 ID，你才能在控制面日志、ingress 日志、execd 日志里找同一条链路。没有它，「用户说失败了」和「服务端说成功了」这种对话会永远卡住。

## 第三层：指标，以及它的基数纪律

Go 组件共享一套 OTLP 配置（endpoint、关闭开关、聚合时序），Python 的生命周期服务器另有 `[otel]` 段。

各组件的指标面不完全一样，我按「谁能回答什么问题」列：

| 组件 | 能回答的问题 |
|---|---|
| 生命周期服务器 | 创建延迟、请求量、错误分布（HTTP metrics 中间件） |
| Kubernetes 控制器 | 分配路径指标、容量指标，以及 controller-runtime 复用的指标 |
| execd | 沙箱 CPU/内存快照与实时流、执行耗时、OOM kill 观测 |
| egress sidecar | 被拒请求数、DNS 查询结果、nftables 更新失败、进程资源 |
| ingress | HTTP 请求与路由、上游连通性分类 |
| node agent | 记录/字节数、有意丢弃、重试、队列深度 |

其中两条设计纪律值得单独记住。

**拒绝与失败必须分开计。** egress 把「被策略拒绝」与「解析器失败」做成两个独立计数器。文档的原话是：对错误的那个告警会把诊断结论完全颠倒。前者是策略在工作，后者是系统坏了——用同一个指标，值班的人永远分不清。

**基数要有界。** egress 不把查询的域名或目的地做成 label；node agent 不把沙箱或 Pod 标识做成 label。这两条限制会让某些排查变难（你无法直接按域名聚合），但换来的是指标系统不会因为用户输入而爆炸。这是一个典型的「先保证可用，再用日志补细节」的取舍。

## 第四层：诊断 API 与它的两种投递

`specs/diagnostic-api.yml` 定义了一条 best-effort 的证据通道：

```text
GET /sandboxes/{id}/diagnostics/context    后端与标识符
GET /sandboxes/{id}/diagnostics/logs       日志内容描述符（scope 必填）
GET /sandboxes/{id}/diagnostics/events     事件内容描述符（scope 必填）
```

`scope` 的取值按后端不同：

| 后端 | logs | events |
|---|---|---|
| Docker | `container` / `all` | `runtime` / `all` |
| Kubernetes | `container` / `all` | `runtime` / `all` |
| Fast Sandbox | 未实现 | `runtime` / `all` |

不支持返回 `DIAGNOSTICS_SCOPE_UNSUPPORTED`；后端没实现返回 `501 DIAGNOSTICS_NOT_IMPLEMENTED`；未知沙箱 404。

响应有两种投递方式：`delivery: inline` 带 `content`，或 `delivery: url` 带 `contentUrl` 与可选过期时间。这里有两条使用纪律，文档写得很直接：

- **SDK 不会去下载 URL 内容**；要自己取。
- **先看 `truncated` 与 `warnings`**，再决定是否把结果当成完整证据。

第二条是很多排障误判的来源：诊断数据本身可能是截断的，把一个截断的日志当成「全部输出」会得出错误结论。

除了这条契约化的通道，服务器还有一套面向运维与 AI 排障的**纯文本 devops 路由**（`api/devops.py`），CLI 的 `osb devops` 就调用它们。契约通道给程序用，纯文本通道给人（或给读文本的 Agent）用——这个分工很实际。

## 第五层：节点级采集，让日志活过沙箱

如果沙箱删了就什么都没了，那上面四层都只能在「还活着」的时候用。Node Agent 是补这一块的：一个可选 DaemonSet，每台 Linux 节点一个采集器，watch 该节点上每个沙箱 Pod 的输出并送到持久存储。

它的设计里有四个决定值得学。

**源与 sink 是编译期注册表，不是插件。** 一个构建嵌入它支持的源，配置只在其中选择；未知名字让 Agent **不 ready** 而不是启动一个残缺管道。运行时不加载任何代码。这条设计对供应链安全很重要：一个能动态加载采集插件的守护进程，等于给集群装了一个远程代码执行入口。

**队列有界，丢弃策略明确。** 默认 `block` 策略在 sink 变慢时对源施加背压——采集变慢，什么也不丢。另一条 `drop` 策略更硬地限制内存，并把丢弃记录进流的完整性标记。全局内存预算、每沙箱队列预算、可选每沙箱限速，防止一个吵闹的沙箱饿死整节点。

**失败即关闭，绝不崩溃循环。** 非法配置、损坏或不匹配的恢复状态、不可用的目标，会让进程**活着但不 ready**，并报出命名原因。它从不静默重置并重新采集，也从不在未验证的目标上推进进度。

**对象布局镜像集群现实。** 每条流成为一个对象族：

```text
<cluster>/<namespace>/<sandbox_id>/<pod_uid>/
  sandbox.log
  sandbox.<generation>.log
  sandbox.finalized.<revision>.json
```

`finalized` 标记是流的「结案陈词」：一份累积的、不可变的快照，列出每个已关闭对象的 size 与 checksum，消费者可以校验。它回答的是事故之后唯一重要的问题：**这些日志我能信吗？**

| 标记状态 | 含义 |
|---|---|
| `complete` | 没有有意丢弃或已知缺口；至少一次投递仍可能含重复 |
| `complete-with-drops` | 至少一条被有意丢弃，受影响的区间已记录 |
| `incomplete` | 至少一个已知区间读不到，或其覆盖无法证明 |

这份诚实的代价是「看起来更差」：Agent 重启后恢复的数据照样投递，但下一个标记是 `incomplete`，因为无法证明一次短命的 rotation 没有在中断期间消失。消费者拿到的是真相，不是乐观。

投递语义是**至少一次、绝不静默丢失**：游标只在 sink 持久接受且源确认后才推进；finalization 本身是两阶段、可崩溃恢复的事务。重复可能，未记账的丢失不允许。

还有两个运维边界：**采集凭据只给 append/read/write，从不给 delete**；删除永远走单独的离线工具。**保留策略是运维的事**，Agent 只采集。桶开了版本控制或 WORM、或存在重叠的生命周期规则，会被当作永久启动错误拒绝——因为那会破坏 Agent 的完整性假设。

## 一个整体判断

把五层放在一起看，这个项目的可观测性有一个一致的态度：**宁可承认不确定，也不给一个看起来漂亮的结论。**

证据都带边界：状态带 reason、诊断带 truncated、日志带 complete/incomplete、指标拒绝高基数、指标把「拒绝」和「坏了」分开、SDK 不替你下载 URL。这套态度在实践中意味着更多次「不确定」，但每一次都是可复核的。

## 本章要点

- 五层证据：状态与原因码、请求 ID、指标、诊断 API、节点级采集。
- `diagnostics/context` 不需要 scope，可在 Pod 分配前查询；Fast Sandbox 的标识 map 为空是诚实而非缺陷。
- 指标有两条纪律：拒绝与失败分开计、label 基数有界。
- 诊断 API 是 best-effort：先看 `truncated`/`warnings`，SDK 不下载 URL。
- Node Agent 用编译期源/sink、有界队列、fail-closed readiness 与完整性标记，把「能信吗」变成可校验的字段。
- 采集凭据没有 delete 权限，保留策略在采集之外。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/middleware/request_id.py` | 请求 ID 的生成与透传 |
| `server/opensandbox_server/middleware/http_metrics.py` | HTTP 指标 |
| `server/opensandbox_server/api/devops.py` | 纯文本排障路由 |
| `server/opensandbox_server/services/diagnostics.py` | 诊断上下文与内容描述符 |
| `components/nodeagent/pkg/marker/` | 完整性标记 |
| `components/nodeagent/pkg/pipeline/` | 有界队列与背压 |
| `docs/guides/telemetry.md` | 各组件指标清单与配置 |
| `docs/architecture/data-plane/node-agent.md` | 采集语义与保证 |
