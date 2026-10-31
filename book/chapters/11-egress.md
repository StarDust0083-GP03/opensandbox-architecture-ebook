# 出向网络：DNS 与 nftables 的两层执行

入向要回答「怎么找到沙箱」，出向要回答一个更难的问题：**怎么让一段模型生成的代码只访问它该访问的地方，且在它尝试绕过时依然有效。**

这类需求容易写成一句「加个白名单」。真正动手时会发现三个问题：白名单按域名写，但实际连接按 IP 走；只做 DNS 过滤时，一次预先解析就能绕过；做包级过滤时，又必须知道域名解析出的 IP 集合，而这集合会变。

这一篇讲 OpenSandbox 的 egress 组件怎么把这三个问题分层解决。

## 形态：共用网络命名空间的 sidecar

egress 以 sidecar 形式与沙箱**共享网络命名空间**，所以每个出向包都经过它，应用无需任何配置，也无法从内部绕过。策略在创建时声明（`networkPolicy`）或由平台注入，之后持续执行。

| 部署 | 形态 |
|---|---|
| Docker | 独立 sidecar 容器；沙箱加入 sidecar 的网络命名空间 |
| Kubernetes（容器） | sidecar 追加到沙箱 Pod，持有 Pod 里**唯一**的 `NET_ADMIN` |
| 池化沙箱 | 预热 Pod 无法按请求加 sidecar——控制必须写进 Pool 模板（`networkPolicy` + `poolRef` 会被拒绝） |
| Fast Sandbox | 每 Fastlet 一个共享 egress 进程，一沙箱一 subject |

第三行是第 7 篇那条约束的另一面；第四行是第 8 篇讲过的共享进程模型。

「只有 sidecar 有 `NET_ADMIN`」这个细节值得单独提：它意味着沙箱主容器**没有能力改网络规则**。执行位置在数据路径上，权限在数据路径之外——这是这套设计能挡住「从内部关掉防火墙」的根本原因。

## Layer 1：DNS

所有 53 端口的流量被重定向到 sidecar 的 DNS 代理。行为只有两种：

- 被拒绝的域名返回 `NXDOMAIN`；
- 允许的域名正常解析到真实上游，并且**解析出的地址被记录下来**。

第二条是两层之间的接口：DNS 层不只是过滤器，还是「域名 → 地址」的发现机制。

## Layer 2：网络（`dns+nft` 模式）

nftables 丢弃一切未被明确允许的流量。允许的流量靠两条路径通过：

- **静态规则**：IP 或 CIDR 直接允许；
- **DNS 学到的地址集**：一个被解析出的 IP 获得**有界租约**（活跃 TCP 连接有一段宽限期），所以「允许」永远不会悄悄变成「永远允许」。

文档对 UDP/QUIC 说得很直白：它们只能依赖 DNS 租约时长，没有连接层面的续租。

这两层是两种成本：

| | 只做 DNS 过滤 | `dns+nft` |
|---|---|---|
| 成本 | 低，通用 | 高，需要 netfilter 能力 |
| 挡住什么 | 按名字访问 | 按名字 + 按包，默认拒绝 |
| 挡不住什么 | 预解析 IP、直连 IP | 已建立连接直到租约到期 |

## 优先级：平台永远压过租户

规则求值的顺序是固定的：

```text
平台 deny.always  →  平台 allow.always  →  用户策略（第一条命中生效）
```

平台覆盖规则来自 sidecar 镜像里的文件（`/var/egress/rules/deny.always`、`allow.always`），每分钟热重载一次。重载有一条前提：**只有 nftables 接受对应静态策略后才发布**；解析或应用失败时保留上一份已接受的内存规则，并继续有资格重试。

这个顺序解决了一个治理问题：租户策略不能打开平台明令禁止的目标。文档的措辞是「平台的 deny 永远赢过你的 allow」。

还有第三层平台控制：`reachable.ports.cap`。它是一份 TCP 端口白名单（一行一个端口或 `lo-hi` 区间），sidecar 取补集，作为一条 always-deny 规则**排在所有用户规则之前**。所以无论沙箱的 `defaultAction` 或 allow 规则怎么写，只有白名单里的端口能通。文件缺失或为空表示没有地板。

## 端口范围规则与它的四条限制

一条规则的 `target` 可以省略而只给 `ports`，于是可以「按 TCP 目的端口」作用域化——`{"action": "deny", "ports": [25]}` 就是禁止出向 SMTP，不管目的地。`target` 与 `ports` 也可以组合，把一条 IP/CIDR 规则限定到特定端口。

限制写得很具体，说明这些是刻意划的线：

- **只支持 TCP**，没有 UDP；
- **每条规则最多 256 个端口**；
- **域名目标不能与 `ports` 组合**——域名规则在 DNS 层求值，那一层没有端口维度，校验时直接拒绝；
- `DELETE /policy` 按 `target` 删除规则，而纯端口规则没有 target，需要用显式的空串 `""` 来删。

还有一条与 DNS 层有关的语义陷阱：**无 target 的 allow 规则，DNS 代理是看不见的**。在 `defaultAction: deny` 下，主机名解析仍然返回 `NXDOMAIN`，所以这类规则只服务「直接拨 IP」的客户端。要按名字访问，就得写显式的域名 allow 规则（而域名规则不能带端口）。

Fast Sandbox profile 目前拒绝 `ports`（返回 400），直到它的执行路径支持为止。这是「能力差异显式声明」的又一个例子。

## 透明 TLS 拦截：能看、能控、能认证

前两层决定「连接能不能发生」。第三层（L7）决定「通过连接的内容能不能被看见与控制」：启用后，出向 HTTP/HTTPS 被重定向进 sidecar 内的 mitmproxy 监听器，它从连接状态恢复真实目的地，并代表沙箱终结 TLS。

文档对它的成熟度描述很精确：**机制可用，配置面仍是实验性的**。拦截、凭据注入、CA 投递都已完成并在生产环境使用；但额外端口与诊断开关可能在发布之间变化。

三个设计决定值得记：

**信任是「投递」的，不是「关掉校验」。** sidecar 导出自己的 CA，沙箱 bootstrap 尽力把它装进系统、NSS、JDK 的信任库。客户端保持证书校验（`curl` 不带 `-k`）。Chromium 系镜像应该带原生 `certutil`，否则 per-user NSS store 更新不了。

**可扩展但不越权。** 内置 system addon 总是先加载，然后按顺序加载 `OPENSANDBOX_EGRESS_MITMPROXY_SCRIPT` 里的逗号分隔 mitmproxy addon。addon 拥有完整 L7 hook 面，但**只能在内置行为之上，不能在其之前**。

**链式上游代理 fail-closed。** 设置 `OPENSANDBOX_EGRESS_UPSTREAM_PROXY` 后，所有被 mitmproxy 处理的出向流量经 `CONNECT` 走代理。规则很硬：endpoint 只接受 `host[:port]` 或单个 `/` 路径，host 必须是字面 IP 或带点的域名（无点名字在 Pod resolver 的 search list 下会解析到不同地址，包含集合可能漏掉实际拨号地址）；不能链式代理的流（无 SNI、`ignore_hosts` 匹配、UDP）**被拒绝而不是直连**；在 `dns+nft` 下代理 endpoint 只对 mitmproxy 进程可达（UID/IP/端口三重 nft 规则），字面代理 IP 永久 seed 进包含集合，DNS 学到的地址用有界 TTL。

## 已知边界

文档把这条链路上的坑列得很清楚，读这些比读特性表更有用：

- **端口 80/443 总是被拦截**，只能加有界的额外端口。Vault 绑定匹配目前只在 80/443 生效——额外端口会被解密和记录，但不会注入凭据。
- **IPv6 与 IPv4 一起拦截**（监听器与重定向规则覆盖两个地址族）。
- **代理未初始化时可见**：sidecar 返回 `503 mitmproxy not ready`，而不是让流量未经检查地通过。
- **SSE 超过约 1 MB 且源站以 TCP RST 关闭连接时可能被截断**——这是标准 TCP 语义，不是拦截 bug。
- **与透明 mesh sidecar 不兼容**：Istio/Envoy 这类注入会与 egress 争抢同一命名空间的出向改写，per-sandbox 策略、透明 MITM、Credential Vault 都不能依赖。文档建议把沙箱 Pod 排除在 mesh 注入之外，或用 CNI 级的机制。
- **gVisor 不支持**：两条重定向路径都需要的 nftables 特性在 gVisor 里没有。要用安全运行时请用 Kata。
- **OSEP-0023 的实验性 revision runtime**（`OPENSANDBOX_EGRESS_EXPERIMENTAL_REVISION_RUNTIME`）是开发专用开关，默认关。它有一条非常保守的失败策略：任何不确定的结果（未确认的变更、会话清理失败、子进程异常退出）都会**锁存为「需要恢复」的粘滞状态**，readiness 下降、nft 写入静止、并安装独立的隔离表直到进程生命期结束；**重启 sidecar 不算已验证的恢复**，应当销毁并重建沙箱。

最后一条读起来沉重，但它是对的方向：涉及 TLS 拦截与凭据注入的子系统，宁可要求重建沙箱，也不要假装恢复成功。

## 关闭时会发生什么

关闭不是一个瞬间动作。sidecar 会**保持 DNS 与网络规则工作**，等在途投递完成（有界窗口），然后才移除规则并刷新遥测。Docker 给 sidecar 9 秒停止预算。

投递是尽力而为：强制终止可能丢事件。supervisor 负责以指数退避重启，带崩溃循环断路器，并在新启动前清理陈旧重定向。

## 可观测性：拒绝原因要分开计

配置了 OTLP 端点时，sidecar 按沙箱导出指标：被拒请求数、DNS 查询结果、nftables 更新失败、进程资源使用。

其中一条设计注意被文档单独点出：**「被策略拒绝」与「解析器失败」是两个刻意分开的计数器**，因为对错误的那个告警会把诊断结论完全颠倒。一个是策略正常工作，一个是系统坏了。

基数也是有界的：查询的域名与目的地**永远不成为 label**。

## 本章要点

- egress 与沙箱共享网络命名空间，执行点在数据路径上；只有 sidecar 持有 `NET_ADMIN`。
- 两层执行：DNS 过滤（便宜、只按名字）与 `dns+nft`（包级默认拒绝，DNS 学到的地址有界租约）。
- 优先级固定：平台 deny → 平台 allow → 用户策略；端口地板作为 always-deny 排在用户规则之前。
- 端口范围规则只支持 TCP、每规则 ≤256 端口、不能与域名组合；无 target 的 allow 对 DNS 层不可见。
- 透明 TLS 拦截把 CA **投递**进信任库而不是关闭校验；链式上游代理 fail-closed。
- 与 mesh 注入、gVisor 不兼容；已知截断与端口限制写在文档里而不是藏着。
- 关闭时保持规则直到在途投递完成；崩溃由 supervisor 退避重启并清理陈旧重定向。
- 被拒与解析失败分开计数，label 基数有界。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `components/egress/pkg/policy/policy.go` | `EgressRule`、`PortRange`、`NetworkPolicy` |
| `components/egress/pkg/policy/always_rules.go` | 平台覆盖规则与端口地板 |
| `components/egress/pkg/dnsproxy/` | DNS 过滤与地址记录 |
| `components/egress/pkg/nftables/` | 集合、租约、原子替换 |
| `components/egress/pkg/credentialvault/` | 凭据存储（第 12 篇） |
| `components/egress/pkg/subject/` | fast-sandbox profile 的 subject 状态机 |
| `docs/architecture/network/egress.md` | 权威表述：优先级、限制、边界 |
