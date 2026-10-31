# 入向网络：endpoint 解析、ingress 与 secure access

客户端要访问沙箱里的服务，第一件事是知道地址。这件事在单机上很简单（`localhost:40001/proxy/8080`），在集群里就很麻烦：Pod IP 会变、Pod 会重建、网关要按租户鉴权、还得支持 WebSocket。

OpenSandbox 的做法是把「怎么访问」变成一个契约端点，然后用三种实现去填它。这一篇讲这三种实现、路由细节，以及一个容易被忽略的副作用——访问会延长沙箱寿命。

## 三种形态，一个契约

`GET /sandboxes/{id}/endpoints/{port}` 的返回永远是「能用的地址 + 必须带的头」，但形态有三类：

| 形态 | 何时使用 | 地址长相 |
|---|---|---|
| 直连 | Docker 宿主映射、Pod IP 可达 | `host:40xxx/proxy/8080` 或 `10.0.0.12:8080` |
| 网关 | Kubernetes + ingress 部署 | `gateway.example.com` + 路由头 |
| 控制面代理 | 两者都不通 | `.../sandboxes/{id}/proxy/{port}` |

文档把第三类定性为**兼容退路，不是快路径**。这个定性很重要：它意味着代理层不需要为吞吐做极致优化，但必须正确——因为它是「其他路都走不通时」的最后一条路。

## ingress 的两种路由模式

ingress 是一台面向所有沙箱的反向代理，**没有 per-sandbox 配置**。它 watch 工作负载已经发布的状态——endpoint 注解、Service FQDN、或 FastPath 控制面——然后在实时缓存里解析每个请求。

路由是一个「沙箱 ID + 端口」的组合，有两种发现方式：

- **头模式（默认）**：`OpenSandbox-Ingress-To: <sandbox-id>-<port>`，或者把同样的信息编码进 host label（`my-sandbox-8080.example.com`）。
- **URI 模式**：路径前两段是这对信息——`/<sandbox-id>/<port>/<path>`——给不能设置头的客户端用。

三种后端的数据来源不同：`BatchSandbox` 从 `sandbox.opensandbox.io/endpoints` 注解读；`agent-sandbox` 读 `status.serviceFQDN`；Fast Sandbox 路由则问 FastPath 解析。

转发前的处理是标准反向代理动作，但都有具体清单：剥掉路由头、加 `X-Forwarded-*`、过滤逐跳头、**精确保留路径与查询串**。

最后一条比看起来重要。ingress 一旦「顺手」规范化路径（比如合并 `//`、解码 `%2F`），就会改变后端看到的语义，某些 Web 框架的路由匹配会因此不同。

## 错误语义：三种失败必须分得开

| 状态 | 含义 |
|---|---|
| `404` | 没有这个沙箱（或已不存在） |
| `503` | 沙箱存在但没就绪，还没有可达的 endpoint |
| `502` | 基础设施级失败（集群 API 错误等） |

这三者的区分决定了客户端能否正确反应：`404` 该放弃，`503` 该重试，`502` 该告警。

第二条还有一条额外的纪律：**沙箱被访问之后，响应原样透传**，包括重定向与错误。ingress 自己从不跟随重定向——这样一条过期的 `Location` 永远不可能把凭据泄漏给无关端点。

Fast Sandbox 侧的等价规则更严格：`FailedPrecondition`（含路由尚未发布）必须以 `503` + `Retry-After` 出现，**绝不能被报成「不存在」**，因为 ingress 不该泄漏「没就绪」与「不存在」的差别（第 8 篇讲过）。

## WebSocket 是一等公民

很多同类代理把 WebSocket 当成「HTTP 的 upgrade 补丁」。OpenSandbox 把它当主路径处理，规则写得很细：

- 升级遵循 RFC 6455 over HTTP/1.1，后端握手预算 **45 秒**；
- 客户端选择的子协议与后端 `Set-Cookie` 头都要穿过握手；
- 应用关闭码原样透传——策略违规（`1008`）或应用自定义码（`4001+`）**不会被改写成 `1000`**；
- 单条消息最多 **64 MiB**，双向，按终端与 Jupyter 的流量规模设计；
- `Connection` 头 token 大小写不敏感匹配，双向流不缓冲对端。

第二条与第三条说明了一个态度：代理层不做「善意改写」。关闭码被改写是最典型的坑——客户端会以为连接正常关闭，于是不重连。

## 在 ingress 前面放 TLS 代理时会发生什么

ingress 自己只说明文 HTTP/1.1，**不做 TLS 终结**。所以生产部署要在它前面放一个 L7 代理。但这里有一个技术约束：

**WebSocket over HTTP/2（RFC 8441）不被接受**，而 RFC 7540 又禁止在普通 h2 请求上带 `Upgrade` 头。所以前面的代理必须把 WebSocket 升级当作经典 HTTP/1.1 投递。

文档给了三档方案，按优先级：

1. 把 `h2` 从面向浏览器的 ALPN 列表里去掉（最可移植，已验证）；
2. 如果代理支持「对 WebSocket 回退 HTTP/1.1，其余保持 h2」，用它；
3. RFC 8441 → RFC 6455 翻译（依赖具体代理，必须用真浏览器验证）。

底线更简单：**永远不要把 HTTP/2 转发给 ingress 本身**，因为升级永远到不了 WebSocket handler。

## 健康检查与被刻意区分开的「网络就绪」

ingress 有两个端点，语义不同：

- `/status.ok` — 存活：进程在跑、在服务。
- `/status.ok/network-readiness` — 一个**影子信号**：对最近窗口内 ingress 打开的 TCP 连接做分类（success、timeout、unreachable、refused 等），当 timeout 与 unreachable 在足够多不同目标上占主导时报 `DEGRADED`。

第二个端点永远返回 `200`，body 是 `OK` 或 `DEGRADED`，并且**从不影响流量**。文档的说法是「给运维看的决策输入，不是负载均衡钩子」。它的指标里刻意没有目标地址和沙箱 ID。

这个设计值得抄：把「我能不能服务」与「网络看起来健康吗」拆成两个信号，前者用于摘流量，后者只用于告警。混在一起时，一次跨节点网络抖动会导致大规模错误摘除。

## secure access：endpoint 的头就是凭据

`secureAccess` 目前只支持 Kubernetes + ingress gateway 模式。开启后，服务器为 endpoint 预置凭据，并把**必需的头随 endpoint 一起返回**。配置了网关签名密钥时，还支持签名的路由令牌。

这里有个安全约束值得单独强调：**endpoint 响应里的头是凭据**。文档在 execd 的 PTY 章节也重复了一次（沙箱级令牌，不要放进 URL 或持久存储）。这类材料一旦被缓存到共享位置，等于把沙箱的访问权发出去了。

`api/proxy.py` 里有一条相关规则：当 endpoint 被改写成服务器代理 URL 时，服务器**只移除 ingress 路由头** `OpenSandbox-Ingress-To`，保留其他必需的头。少移一个会泄漏路由信息，多移一个会让后端认证失败。

## 访问会延长寿命：renew-intent

这是一个容易被忽略的联动。开启后，ingress 在每个被代理的请求**解析出沙箱之后**发布一次 **renew-intent** 事件；生命周期服务器据此延长那些在创建时选择了续期的沙箱。

四个性质定义了这个机制：

- **按沙箱节流**，且**尽力而为**：空闲沙箱照样过期，繁忙沙箱无需客户端改动就能续命。
- **单请求可以退出**：`OpenSandbox-Access-Renew: skip`，只认精确值 `skip`，未知值忽略（向前兼容）。典型用途是健康探针不要延长沙箱寿命。
- **两条代理路径都遵守**：ingress 网关和服务器 `/sandboxes/{id}/proxy/{port}` 都实现，所以行为不取决于流量走哪条路。
- **头在转发上游前被剥掉**，和后端应用看到的路由头一样。

WebSocket 升级会在握手请求上携带该头，但长连接不会产生每请求的续期意图——这条边界在文档里写明了，因为「长连接算不算活跃」没有唯一答案，明说比猜好。

同一套机制还有一个近亲：OSEP-0024 的**空闲暂停**。它增加了 `OpenSandbox-Skip-Activity: skip` 哨兵，用于让观测探针不重置它自己测量的时钟。文档还补了一句很有意思的分析：伪造这个头只会伤到自己——自己的沙箱会显得更空闲、更可能在使用中被暂停，而下一个普通请求会把它唤醒。

## 本章要点

- endpoint 有三种形态：直连、网关、控制面代理；代理是兼容退路不是快路径。
- ingress 无 per-sandbox 配置，靠 watch 工作负载已有状态；头模式与 URI 模式并存。
- 错误语义三分类（404/503/502），到达后响应原样透传，绝不跟随重定向。
- WebSocket 是一等路径：45 秒握手预算、64 MiB 消息、关闭码不改写、不缓冲。
- ingress 不做 TLS 终结；前置代理必须用 HTTP/1.1 承载 WebSocket 升级。
- `/status.ok/network-readiness` 是影子信号，永不 gate 流量。
- `secureAccess` 的头就是凭据；代理只移除路由头，保留其余。
- renew-intent 按沙箱节流、尽力而为，`OpenSandbox-Access-Renew: skip` 可单请求退出，两条路径行为一致。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `components/ingress/pkg/proxy/header.go` | 路由头、续期头、逐跳头清单 |
| `components/ingress/pkg/proxy/` | 路由解析、WebSocket、错误映射、健康端点 |
| `components/ingress/pkg/proxy/connectivity/` | 连接分类与 DEGRADED 判定 |
| `server/opensandbox_server/api/proxy.py` | 服务器代理与头处理 |
| `docs/architecture/network/ingress.md` | 路由模式、错误语义、TLS 约束 |
| `docs/guides/secure-access.md` | endpoint 凭据与签名路由 |
