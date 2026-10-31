# 数据平面：execd 的命令、文件与流式语义

前面八篇都在沙箱外面。这一篇进到里面。

`components/execd/` 是一个 Go 写的守护进程，用 Gin 做 HTTP，默认监听 `44772`。它是 OpenSandbox 的沙箱内执行 API 参考实现：命令、文件、目录、代码上下文、PTY、指标、隔离会话。它同时还能当沙箱的 init 进程。

它会出现在三种部署形态里：Docker 里由服务器暂存二进制；Kubernetes 里由 init 容器把 execd 与 `bootstrap.sh` 从 `execd_image` 拷进 `emptyDir`，再挂给主容器；Fast Sandbox 里烘在 guest 镜像里。

## 一个 API，一句令牌

所有端点都在同一个 API 下。开启访问令牌后，每个端点都要求 `X-EXECD-ACCESS-TOKEN`，**只有存活与就绪探针永远开放**。

| 区域 | 端点 | 说明 |
|---|---|---|
| 健康 | `/ping`、`/ready` | 无需令牌 |
| 命令 | `/command`、状态、日志 | 前台流式或后台轮询 |
| Shell 会话 | `/session`、`/session/{id}/run` | 跨调用保持状态 |
| 代码 | `/code`、`/code/contexts` | Jupyter 内核上下文 |
| 终端 | `/pty` | WebSocket 接入 |
| 文件 | `/files/*`、`/directories` | 完整文件系统面 |
| 隔离会话 | `/v1/isolated/*` | 私有命名空间的 shell |
| 指标 | `/metrics`、`/metrics/watch` | 快照与实时流 |
| 反向代理 | `/proxy/{port}` | 一个宿主端口触达所有端口 |

这个令牌的语义要看清：它是**沙箱级**的凭据，不是「终端票」。文档明确说它应该通过授权的 endpoint 解析流程拿到，不要放进 URL 或浏览器持久存储，生产环境要用 WSS——因为 base64url 不是加密。

## 命令：两种形式、两种模式、三条语义

命令有两种提交形式：

- **shell 语法**：管道、重定向、环境展开，就是你在提示符下会敲的东西；
- **直接 argv**：程序 + 字面参数，不经 shell 解析。当命令的一部分来自不可信输入时更安全，并且能精确保留空串和特殊字符。

两种模式：

- **前台**：命令运行期间以 SSE 实时推流，返回后流关闭；
- **后台**：立即返回，之后轮询状态、增量取日志。已完成的后台输出保留 24 小时，**运行中的命令永不被清理**。

三条语义是第 3 篇提到的契约条款，这里说清它们为什么必须存在。

**行尾保留。** 前台 `stdout`/`stderr` 事件的文本保留命令原始的行尾（`\n`、`\r`、`\r\n`）。按流拼接事件文本即可还原输出，最后一个事件可能是不完整的一段。这一条看似琐碎，但它决定了客户端能否实现「逐行渲染进度条」这类需求——如果 execd 悄悄把 `\r` 换成 `\n`，进度条就变成几千行日志。

**成功也有退出码。** 前台 `POST /command` 以 `execution_complete` 携带 `exit_code: 0` 结束。这个字段是**可选**的：后台启动确认里没有，代码执行事件里也没有，缺失不代表成功。非零退出保留在 `error` 事件的 `error.exit_code` 上。

**流提前结束不构成成功。** 在终止事件之前断流，只能当作不确定。这一条是对「客户端乐观推断」的明确拒绝。

文档还记录了一次真实兼容事故，值得完整转述，因为它示范了「协议演进如何变成客户端崩溃」：`sandbox-api 1.0.18` 之前的 SDK 不认识 `exit_code` 字段。当消费者的 classpath 解析到不兼容的 kotlinx-serialization 运行时时，解码未知字段会以 `AbstractMethodError` 崩溃——**命令在沙箱里跑完了，客户端调用永不返回，也不打日志**。

上游的处置分两层：短期给 `EXECD_SSE_LEGACY_COMPAT=true`，让 execd 在两个事件里都省掉该字段（成功的命令不损失信息，因为值恒为 0，新 SDK 会推断）；长期是让 SDK 遮蔽自己的序列化运行时（`sandbox-api 1.0.18` 起），从而消除「每加一个字段就多一个风险」的结构问题。这段的价值在于它承认了「加一个可选字段」在强类型客户端生态里不是安全的操作。

## Bash 会话：合并流与 16 MiB 的行

`/session` 提供一个跨调用有状态的 shell。两次 `run` 之间共享 shell 状态（`export X=1` 对下一次可见），这是它和命令执行的根本区别。

实现上有几个具体决定：

- **合并流**。会话运行只推一路 `stdout` 事件，会话的 stderr 被合进去。因为 shell 会话的语义是「一个终端」，分离两路反而失真。
- **空行保留**。只有行尾的空行也是一个事件。
- **超长行切分**。超过 16 MiB 的行被拆成多个事件，只有最后一个带行尾。
- **shell 回退**。镜像有 bash 就用 bash，最小镜像回退到 POSIX `sh`——此时命令必须是 `sh` 兼容的。Windows 沙箱也支持（环境变量名大小写不敏感，批处理文件需要 shell 语法）。

## 代码执行：Jupyter 是后端

代码上下文是 execd 管理的持久 Jupyter 内核。你在一个上下文里执行代码，收到流式结果（标准输出、执行结果、错误），多次调用共享状态——和 notebook 一样。

官方 code-interpreter 镜像（在 `opensandbox-group/sandbox-images` 仓库）带 Python、Java、Node.js、Go 运行时和对应内核。语言版本由镜像控制，经 `PYTHON_VERSION` 这类环境变量选择。

这里有一个架构上的分层：Code Interpreter SDK 是可选的高层客户端；底层的执行 API 通过普通沙箱 SDK 或直接调 execd 也能用。第 3 篇提到「shell-only 镜像不支持 notebook 式代码执行」——因为代码执行的实现依赖 Jupyter 与已装内核，不是 execd 凭空提供的。

## PTY：一个持有者，若干观看者

终端会话是真的 PTY，走 WebSocket，不是「包装过的命令」。所以编辑器、REPL、Agent CLI 这类交互程序能正常工作。

共享模型是刻意设计的：**恰好一个 holder 拥有键盘，任意多个 viewer 可以观看**。viewer 先回放已打印内容，再跟实时输出。用途很具体：监督、调试助手、让第二个人看着 Agent 干活但不给它输入。

浏览器有一个现实问题：WebSocket 握手不能带任意头。所以 `GET /pty/{sessionId}/ws` 允许用子协议传凭据：offer `opensandbox-pty` 应用子协议 + 一个凭据子协议，后缀是 execd 令牌的**无填充 base64url**。execd 验证后移除凭据项，只协商 `opensandbox-pty`。

规则写得很细，说明这块踩过坑：

- 显式 `X-EXECD-ACCESS-TOKEN` 头优先，**即使它是空或非法**，绝不回退到子协议；
- 重复凭据、编码错误、没有应用子协议只有凭据，都返回 `401`；
- 既有走头的客户端不需要提供子协议；
- 其他 HTTP API（含 PTY 创建与删除）仍然要求头。

## 文件：先暂存再改名

文件 API 覆盖整棵文件系统树：流式上传下载、列举、搜索、改权限、替换内容、移动、删除。

上传有一个值得学的实现细节：**先写到目标旁边的暂存文件，再 rename 就位**。这样一次中途失败的复制不会截断它正在替换的旧文件。前提是对所在目录有写权限；当只有目标文件可写（例如 `0555` 目录里的 `0666` 文件）时，execd 回退到原地写入——上传仍会成功，但部分失败可能截断旧内容。

几个附带承诺：

- 替换保留目标的 mode，包括 setuid/setgid/sticky 位，并在平台允许时保留 owner/group；
- 目标已存在时，暂存文件私有创建，且从目录列举与搜索结果里隐藏；
- 隔离会话的上传路由（`/v1/isolated/session/:sessionId/files/upload`）**不做暂存**，保持原地行为。

**以指定身份执行文件操作**是一个更强的特性：Linux 上，可信后端可以在路径前加 `/v1/filesystem/{uid}/{gid}`，让操作以该身份执行。每个请求跑在独立 worker 进程里，用选定的凭据；补充组来自选定用户的系统账户（未知数字 UID 没有补充组）；worker **不继承** execd 的补充组，也不继承 execd 的环境。相对路径从 `/` 解析，`~` 用选定账户的家目录（未知 UID 用 `/`）。

但要看清边界，文档自己写了两条：

1. 它不提供独立的 mount namespace，**不是租户隔离边界**；
2. 上传的 `owner`/`group`/`mode` 是目标元数据，不能给 worker 提升权限。

这个特性解决的是「同一个沙箱里多个会话共用一个工作区」的权限问题：给每个会话分配独立用户与目录，写权限留给属主。文档提醒删除与重命名权限取决于父目录，所以只读文件权限不够。

## 隔离会话：bubblewrap 的每执行命名空间

隔离会话在一个 per-execution 的 bubblewrap 命名空间里跑 shell，每次新建：私有挂载视图与身份。显式 bind mount 把宿主路径带进来（只读或读写），写入被限制在允许列表内，并且校验的是**完全解析后的路径**，所以符号链接不能把 bind 重定向到列表之外。会话创建会报告当前环境支持什么，调用方据此调整而不是猜。

成本数字很清楚：创建一个会话约 100 ms（execd 会短暂等待以检测立即退出的子进程），之后每次 `run` 复用同一个 bash，开销可忽略。所以它适合「一次 sandbox、多次隔离任务」的场景（RL rollout、批量判题、多工具 Agent），不适合跨语言内核（用 `/code`）、交互式 REPL（用 `/session`）、或硬信任边界（用 gVisor/Kata）。

契约里的 `diff`/`commit` 没实现，返回 `503`，`/v1/isolated/capabilities` 如实报告 `diff_supported: false`、`commit_supported: false`。这是第 3 篇「契约覆盖 ≠ 实现能力」的实例。

## 分层防护与「它到底生效了吗」

execd 对沙箱里跑的一切做了纵深防御，四层：

1. **沙箱边界**：资源限制、身份、隔离运行时（runc/gVisor/Kata/Firecracker），在创建时选定；
2. **加固地板**：运维开启后，每个用户进程经原生 launcher 启动，drop capabilities、设 `no_new_privs`、装 syscall 拒绝列表、用 Landlock 限制文件系统写；
3. **隔离会话**：上面的 per-execution 命名空间；
4. **可选 eBPF 审计**：记录 exec、connect、权限变更，作用域是沙箱自己的 cgroup，作为单独的 `execd-ebpf` 构建变体发布，不在默认镜像里。

第四层之外的每一层都会**报告自己的状态**：active、degraded 或 unsupported。这条设计比「我们有四层防护」重要得多——客户端能验证实际生效的是哪几层，而不是假设。

同时有一条必须说清的边界：`execd` 的生命周期钩子是平台配置的**可信代码**，在沙箱内运行。文档原话是「它们是便利，不是安全控制」。

## 当 init 与「每次分配一个新身份」

execd 可以当沙箱的 init 进程：回收孤儿进程、把信号转发给 entrypoint、把自己的退出码传给运行时，于是 `Terminated`/`Failed` 反映的是你的进程真的做了什么，长会话也不会积累僵尸进程。

更微妙的是**预热复用时的身份问题**。沙箱可以在池里预热并被不同分配复用。execd 让这件事安全：平台在分配时下发一次 per-allocation 绑定（沙箱 ID、环境、访问令牌、生命周期钩子），execd 在 API 开放之前**原子地应用**它。在等待绑定期间（以及启动完成之前），所有业务端点返回 `503`，只有 `/ping` 与 `/ready` 响应。

这样环境变量、令牌、遥测归属就不会从上一个沙箱泄漏到下一个。这是「预热」这个优化在数据面必须付的代价，也是第 7 篇「池化带来 API 约束」的另一半。

## 指标与代理

execd 暴露本地 CPU/内存指标：快照（`/metrics`）与实时流（`/metrics/watch`）。平台配置了 OTLP 端点时，还导出 OpenTelemetry 指标，并归属到沙箱 ID 与 allocation。

内置反向代理 `/proxy/{port}` 是第 6 篇的主角：一个宿主端口触达所有容器端口。它还转发 `Upgrade`、`Connection` 等头，所以 HTTP、SSE、WebSocket 共用同一映射。

## 本章要点

- execd 是沙箱内执行 API 的参考实现，默认 44772，一个沙箱级令牌保护所有端点（探针除外）。
- 命令支持 shell 语法与直接 argv；前台 SSE、后台轮询；行尾保留、成功带 `exit_code`、断流不等于成功。
- 会话合并 stderr、保留空行、超 16 MiB 行切分、bash→sh 回退。
- PTY 是一持有者多观看者，浏览器可用子协议传 base64url 令牌；显式头优先且为空也不回退。
- 文件上传先暂存再改名；身份前缀 `/v1/filesystem/{uid}/{gid}` 提供执行身份，但不是租户边界。
- 隔离会话是 per-execution bubblewrap，约 100 ms 创建，diff/commit 未实现。
- 防护分层且自报状态；生命周期钩子是可信代码，不是安全控制。
- 预热复用靠 per-allocation 绑定保证环境与令牌不泄漏，未绑定前业务端点 503。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `components/execd/pkg/web/controller/command.go` | 命令执行与 SSE 生命周期 |
| `components/execd/pkg/web/model/codeinterpreting.go` | 事件模型与 `ServerStreamEvent` |
| `components/execd/pkg/web/controller/filesystem_upload.go` | 暂存 + rename |
| `components/execd/pkg/web/controller/pty_ws.go` | PTY 与子协议认证 |
| `components/execd/pkg/isolation/` | bubblewrap 隔离会话 |
| `components/execd/pkg/binding/` | per-allocation 绑定 |
| `docs/architecture/data-plane/execd.md` | 能力全景与语义权威表述 |
