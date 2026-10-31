# 凭证保管：让 Agent 用得上密钥，却拿不到密钥

第 11 篇留下的口子在这里补上。

Agent 要干活就得调外部服务：模型 API、Git 托管、制品库、内部微服务。这些都要密钥。但如果密钥进了沙箱，它就同时进了环境变量、命令行、文件系统和日志——而这四样都是模型生成代码可以读取、也可以在不经意间送出去的地方。一次提示注入就足够把它带走。

Credential Vault 要解决的就是这个矛盾：**工作负载能发出已认证的请求，但自己不持有凭据。**

## 请求流：谁在什么时候注入

一次完整流程是六步：

```text
1. 生命周期服务器给沙箱挂上 egress sidecar
2. 宿主机侧 SDK 把凭据与绑定写进 sidecar 的 Credential Vault API
3. 沙箱进程用假值或空值运行（例如 ANTHROPIC_API_KEY=fake）
4. 沙箱发出 HTTPS 请求，sidecar 的透明 MITM 检查请求元数据
5. 若恰好一个绑定匹配 scheme/host/port/method/path，注入认证头与占位符替换
6. 响应与 vault 读取接口中的秘密值被脱敏
```

第 3 步是这套设计的核心技巧：**工具链以为自己有密钥**。Claude Code、git、curl、npm 都不需要改配置，它们照常从环境变量读一个值，只是那个值是假的；真正认证发生在沙箱外面。文档把它总结成一句：CI 与工具正常工作流不变，秘密不进沙箱的环境、命令行、文件系统与日志。

有一段实现细节很值得学。MITM 进程用的**活跃 vault** 通过 sidecar 内的本地 Unix domain socket 提供，**沙箱工作负载无法经普通代理路径取到**。每次新流用缓存的不透明快照 tag 带 `If-None-Match` 去条件读：

- `304 Not Modified` 复用同一份不可变进程内快照，**不渲染也不传输秘密值**；
- `200 OK` 带回完整校验过的新快照与新 `ETag`；
- 创建与 patch 的响应只有在 store 已经改掉 tag 之后才确认，所以下一个流立刻看到新凭据，没有 cache-TTL 延迟。

不透明 tag 与公开的 vault revision 是两件事：删掉再重建 vault，公开 revision 可能回到 `1`，但重建后的 vault 拿到的是不同的 tag，**不可能复用已删除快照里的明文**。

## 这个依赖是硬依赖

文档在这里用了一个很重的警告，值得完整理解：私有活跃 vault socket 不只是「匹配绑定时才需要」，而是**对所有被 Credential Proxy 拦截的流量都是硬可用性依赖**。

任何超时、连接拒绝、意外 EOF、`5xx`、畸形快照、非法 `ETag`，都会清掉受影响明文缓存，并在任何上游转发之前 **fail closed**：

- body 小且已被安全缓冲的请求，收到本地 `503`；
- 已开始流式传输、可能跨过阈值、或长度未知的请求，**直接断开**，因为 mitmproxy 在流式传输开始之后无法安全地合成一个本地响应。

这条约束把「凭据保管」从纯功能变成了数据面可用性的一部分：运维必须监控 sidecar 与那个私有 socket。这是一个诚实的取舍——宁可 503，也不要「没注入凭据的请求」被悄悄放行。

## 五种认证方式与占位符替换

绑定的 `auth` 描述凭据如何被渲染进出向请求：

| 类型 | 效果 |
|---|---|
| `bearer` | `Authorization: Bearer <credential>` |
| `basic` | `Authorization: Basic <credential>`（值必须已是 base64 的 `user:pass`） |
| `apiKey` | 把值注入指定头名（如 `x-api-key`） |
| `customHeaders` | 多个头，各自由不同凭据支撑 |
| `passthrough` | 不注入头，配合 `substitutions` 用 |

`passthrough` 存在的原因很实际：有些上游 API 要凭据在路径、查询串或 body 里，而不是头里。所以有占位符替换：

```python
auth={"type":"passthrough",
      "substitutions":[{"credential":"client-secret",
                        "placeholder":"__client_secret__",
                        "in":["body","query"]}]}
```

替换默认关闭，且是**精确、字面、大小写敏感**的。四个面各自的行为：

- `path`：替换后 URL 编码；若重写后的路径出现歧义段、编码分隔符、或像遍历的内容，sidecar **拒绝请求**，而不是把带秘密的 URL 转发出去；
- `query`：URL 编码；
- `header`：排除逐跳与安全敏感头（`Host`、`Content-Length`、转发头）；替换在注入 auth 头之前运行，所以不会重写自己刚创建的凭据头；
- `body`：只处理 UTF-8；JSON 按 JSON 字符串内容编码（占位符要放在引号内）；form-urlencoded 按表单编码；压缩体与 multipart 跳过。

还有一条「不二次扫描」的规则：一层的所有替换都基于**原始请求文本**一次完成，插入的值不会被再扫描一遍。这样避免一个秘密意外改写另一个秘密。body 被重写后会更新 `Content-Length` 并移除 `Transfer-Encoding`，因为转发出去的已经是定长的缓冲体。

## 路径歧义守卫

这是全章最精细的一段。sidecar 只对「本来会收到凭据注入」的请求检查路径。以下情况返回 `403`：

- 路径在任意百分号解码深度上包含点段（`..`）或反斜杠（`\`、`%5c`）；
- 百分号解码在守卫的迭代上限内不收敛。

理由是这些是遍历或路径混淆原语，合法客户端不会发。

但编码斜杠（`%2f`，含 `%252f` 这类嵌套形式）是**允许**的——只要路径的每一层解码都匹配同一个绑定。这个例外不是妥协，而是必要：npm 的作用域仓库路径（`/@scope%2fname`）和某些内部 pypi 代理的双重编码下载 URL（`pkg%252F1.0`）就长这样。如果一刀切禁止，正常包安装会坏；而如果放任不管，解码可能跨出绑定边界。规则因此写成「**每层解码都必须落在同一绑定内**」。

失败方式也分层：小且 body 已知的请求返回 `403`；超过 mitmproxy 流式阈值（约 1 MiB）或长度未知（chunked）的请求无法在流式传输时合成 `403`，sidecar 直接断开连接。两种情况都保证请求**从不被转发到上游**。

## 要求与它的历史痕迹

Vault 的前置条件写得很具体，每一条都能看出踩过的坑：

- 服务器要设 `[egress].mode = "dns+nft"`。**只有 DNS 过滤时 Vault 拒绝激活**，因为直连 IP 可以绕过 DNS 策略。
- 创建请求必须带出向网络策略，并且**应当**用 `defaultAction="deny"`，显式允许每个绑定引用的主机。
- `defaultAction` 默认允许（default-allow）在兼容期内仍被接受，但**会发安全警告**，并计划在未来收紧。文档的建议是迁移到 deny。
- 沙箱 Pod 不能同时注入透明 mesh sidecar。
- 镜像里要有真正要跑的工具（Claude Code 需要 Node/npm 的镜像）。

第 3 条是一个典型的「兼容期设计」：既不能立刻拒绝旧写法的用户，又不能假装它是安全的，于是既接受又警告，并公开了收紧意图。

## 暂停/恢复：Kubernetes 上会丢，Fast Sandbox 不会

这条差异非常具体，必须分清：

**Kubernetes 容器后端**：vault 是 egress sidecar 的**进程内内存**，不在沙箱 rootfs 里，也不在 `BatchSandbox` Pod 模板里。暂停会删 Pod，恢复后的新 sidecar **vault 是空的**。恢复后必须由可信客户端重新创建凭据与绑定，才能继续注入。

文档给了配套的操作纪律：把原始 vault 请求或等效的 secret-manager 引用保存在沙箱之外的可信控制面；沙箱回到 `Running` 后、在允许依赖这些凭据的工作之前，重新调用 create。**不要把真实凭据值放进沙箱 metadata、环境变量、快照或日志。**

Docker 的 pause/unpause 保留容器进程，但 egress sidecar 的替换或重启同样会产生空 vault。

**Fast Sandbox**：vault 由 egress 持有：服务器经 FastPath handler relay 把完整 vault revision 推给 egress，relay 响应就是同步的 egress 校验确认。vault **持久化在 egress 本地**（内存 + 每沙箱一个明文 v1 文件，放在 Pod 作用域的 `emptyDir`），所以：

- Pod 内的 egress 容器重启，Fastlet 重放绑定时可本地恢复；
- Kubernetes 暂停/恢复会把 vault 文件带进快照 bundle（teardown 之前严格导出，恢复时重新打上运行时围栏再导入）。

代价是那个卷进入**凭据信任面**：用 `medium: Memory` 挂载可以让明文不落节点磁盘。文档原话是「vault 卷是凭据信任面的一部分」。

这条对比是全书里「同一契约、不同后端语义」最尖锐的例子之一：同样叫「恢复沙箱」，一个要求你重新注入凭据，一个自动带着走。第 3 篇的兼容性表就是为了避免这种差异变成线上事故。

## 验收与配置的安全旋钮

sidecar 有三个与 TLS/作用域相关的环境变量，都是默认关：

| 变量 | 作用 |
|---|---|
| `OPENSANDBOX_EGRESS_CREDENTIAL_VAULT_REQUIRE_TLS` | 写操作要求 TLS、loopback，或来自受信代理的 `X-Forwarded-Proto: https` |
| `OPENSANDBOX_EGRESS_CREDENTIAL_VAULT_TRUSTED_PROXY_CIDRS` | 哪些对端有权断言 `X-Forwarded-Proto`，其他来源的转发传输头一律忽略 |
| `OPENSANDBOX_EGRESS_CREDENTIAL_VAULT_REQUIRE_SCOPED_MATCH` | 每个绑定必须显式给非空 `match.methods` 与 `match.paths`，且不能用 `/*` |

第三个是一个 fail-closed 的集成守卫。文档特意说明它**不会**从别的平台 API 工具定义里推断 methods/paths——调用方必须自己把意图的方法与路径写进每个绑定，并核对脱敏后的回读。这条说明防的是「自动生成宽松绑定」这类看似省事、实则放大爆炸半径的做法。

## 本章要点

- Vault 让工作负载发出已认证请求但自己不持有凭据；工具链用假值运行。
- 活跃 vault 经 sidecar 内 Unix socket 提供，沙箱无法经代理路径取到；不透明 tag + 条件读避免重复传输秘密。
- 这个 socket 是全部被拦截流量的硬依赖，异常一律 fail closed（小 body 503，流式直接断开）。
- 五种认证方式 + 可选占位符替换；替换精确字面、单遍、更新 `Content-Length`。
- 路径守卫拒绝遍历/混淆原语，但允许每层解码都落在同一绑定内的编码斜杠。
- 要求 `dns+nft` 与 deny 默认策略；default-allow 仍被接受但会警告。
- Kubernetes 容器后端暂停后 vault 为空，需重新注入；Fast Sandbox 会把 vault 带进检查点（明文卷属于信任面）。
- 三个 TLS/作用域旋钮默认关闭，`REQUIRE_SCOPED_MATCH` 是 fail-closed 守卫。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `components/egress/pkg/credentialvault/` | vault store、绑定匹配、脱敏 |
| `components/egress/mitmscripts/` | 请求改写、替换、路径守卫 |
| `server/opensandbox_server/api/credential_vault.py` | 服务端路由与 fsb relay |
| `docs/guides/credential-vault.md` | 要求、认证类型、路径守卫、暂停语义 |
| `oseps/0012-credential-vault.md` | 设计提案 |
| `oseps/0022-multi-sandbox-egress-control-plane.md` | 共享 egress 与 vault 传递 |
