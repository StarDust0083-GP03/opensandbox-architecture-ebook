# Docker 后端：一个宿主端口装下整个容器

单机 Docker 是最容易被低估的后端。它没有调度器、没有 CRD，看起来只是「调 Docker API 建个容器」。但要在同一台机器上同时跑几十个沙箱、每个沙箱里跑着任意多个服务，还要让它们都能被访问，需要解决一个具体问题：端口的组合爆炸。

这一篇就从这个端口问题开始。

## 问题：一个沙箱里有多少个端口

假设每个沙箱要暴露 8080（应用）、5900（VNC）、8888（Jupyter）、44772（execd）。如果每个容器端口都映射到宿主，那么 30 个沙箱需要 120 个宿主端口，还要保证不冲突、要维护防火墙规则、要在容器重建后重新分配。而且——这是最关键的——**端口的数量由用户镜像决定，服务器事先不知道**。

OpenSandbox 的解法是：**每个沙箱只映射一个宿主端口**。

## 一个端口的路由原理

`execd` 内置了一个轻量反向代理。所有发往 `/proxy/{port}/...` 的请求，被转发到同一容器内的 `127.0.0.1:{port}`。

于是端到端路径变成：

```text
浏览器  →  host:40xxx/proxy/8080/app   →  execd (44772)  →  127.0.0.1:8080/app
         →  host:40xxx/proxy/5900       →  execd          →  127.0.0.1:5900
```

服务器只绑定 execd 的代理端口（容器内 44772），并给它打上标签 `opensandbox.io/embedding-proxy-port`。调用方通过 `get_endpoint(..., port=8080)` 拿到 `{public_host}:{host_proxy_port}/proxy/8080`。

三个后果值得单独说：

**端口冲突消失。** 每个沙箱一个宿主端口，机器上能放的沙箱数量由端口范围和资源决定，而不是由每个沙箱暴露多少服务决定。

**协议自动兼容。** 代理保留 `Upgrade`、`Connection` 等头，所以 HTTP、SSE、WebSocket 共用同一个映射，不需要为 VNC 或终端额外开洞。这一条直接决定了浏览器里的远程桌面能用。

**防火墙规则简化。** 只需要对一段宿主端口开口，而不是每个沙箱的每个服务。

还有一个可选的第二映射：`opensandbox.io/http-port`，给 8080 一个「不含 `/proxy/` 前缀」的常规 HTTP 入口。这是为那些不能处理路径前缀的客户端准备的——比如某些 aio-sandbox 镜像自带 API，它不知道 `/proxy/8080` 是外挂的路径。

## 什么时候绕过代理

代理不是唯一路径。`get_endpoint(..., resolve_internal=True)` 会返回容器的 Docker 网络 IP（例如 `172.17.0.3:5900`），跳过宿主映射。

但文档给了一条很实际的警告：**服务器跑在容器里，不等于它能路由到沙箱**。一个接在 Compose 网络上的服务器，默认无法访问 Docker 默认 bridge 上的容器。所以「内部地址」只在调用方确实在同一网络中时可用。

这条限制导致了一个配置陷阱，文档专门写了一整节：当服务器容器通过挂载的 socket 在默认 bridge 上创建沙箱时，需要

```toml
[proxy]
resolve_internal = false

[docker]
network_mode = "bridge"
host_ip = "host.docker.internal"
```

并且服务器容器要能解析 `host.docker.internal`（Linux 上需要 `extra_hosts: ["host.docker.internal:host-gateway"]`）。文档还补了一句历史教训：旧的 `server/v0.2.3` 镜像把内部 IP 解析硬编码在两条代理路径里，设置 `resolve_internal = false` 也救不回来，SDK 的就绪检查会超时。修完之后必须拉新镜像并重建容器。

这段的价值不在配置本身，而在于它示范了「兼容性事实也要写进文档」：一个已经修掉的老版本的坑，仍然值得保留，因为生产环境里还跑着老镜像。

## 网络模式与它们各自的代价

| 模式 | 行为 | 代价 |
|---|---|---|
| `bridge`（默认） | 独立桥接网络，只映射代理端口 | 需要端口分配；`networkPolicy` 只用这个模式 |
| `host` | 共享宿主网络栈 | 实际只能一个沙箱一台机，除非手工错开端口 |
| `none` | 无网络 | 只能离线跑代码 |
| `pasta` / `slirp4netns` | 用户态网络（podman rootless） | 没有宿主可路由的容器 IP，只能走映射端口 |
| 自定义网络名 | 用户定义的 Docker network | 需要网络已存在，启动时校验 |

`networkPolicy` 只在 `bridge` 下支持，代码里是显式拒绝的（`networkPolicy is not supported when docker network_mode=host`）。原因不难推：egress sidecar 需要和沙箱共享网络命名空间并使用 iptables/nftables，用户态网络模式下没有可用的 bridge 与 netfilter 路径。

用户态模式还有一个细节容易被误解：这类模式下 `docker inspect` 返回的 `NetworkSettings.IPAddress` 是空的，`Networks` 也没有条目。所以 `resolve_internal=True` 在这类模式下不会报错，而是回退到宿主映射地址——和「共享 egress sidecar 网络命名空间」时的回退一致。这是「让 API 保持可用而不是给一个无意义的错误」的选择。

## execd 是怎么进容器的

沙箱镜像是用户给的，里面没有 execd。所以运行时要做一次注入：

1. 从 `[runtime].execd_image` 里把 execd 二进制**暂存**到沙箱容器；
2. 安装一个 bootstrap 启动器；
3. 由 bootstrap 启动用户 entrypoint。

文档把这个过程描述为「staging」，代码在 `services/docker/container_ops.py`。第 9 篇会从 execd 的视角再看同一件事：execd 可以当 init（`execd --init`），也可以只做旁挂守护进程，两种模式的启动顺序不同。

注入式的好处是镜像不需要为 OpenSandbox 定制；坏处是每次创建都要复制二进制，并且镜像里必须有 `cp`/`tar` 之类的工具。这也是为什么 Fast Sandbox 走另一条路：模板构建时就把 execd 烘进 guest 镜像，创建时不需要注入。

## 生命周期操作的具体含义

Docker 后端把抽象方法翻译成容器操作，其中有两个容易被问到的点。

**暂停的顺序。** 有 egress sidecar 时，暂停先冻结沙箱主容器，再冻结 sidecar；恢复先解冻 sidecar，再解冻主容器。理由很直接：主容器一旦解冻就会发网络请求，此时 sidecar 必须已经在工作，否则第一批包会被丢掉或被默认策略拒绝。

**删除与冲突。** 文档明确了并发请求的映射：删除正在进行中、或容器已不在暂停/恢复所需状态时返回 `409`；主容器不存在返回 `404`。而 sidecar 相关失败、回滚失败保留 `500`。这个分级说明作者把「用户操作冲突」和「系统内部错误」严格分开了。

## 快照：commit 成本地镜像

Docker 的公开快照 API 实现是 `docker commit`：把运行中的容器提交成本地 OCI 镜像，然后从该镜像创建新沙箱。

这带来两个性质：

- **快照是本地制品**，不推送 registry，所以只在这台机器上可恢复；
- **快照包含 rootfs，不包含内存**，所以恢复的是文件系统，不是进程现场。

对比一下 Kubernetes 后端（提交镜像并推送到 registry）和 Fast Sandbox（检查点包含 rootfs + vmstate + memory），三种后端的「快照」语义强度完全不同。这也是第 3 篇强调「契约里有这个操作，不等于所有后端语义一致」的实例。

## 一个被隐藏的目录：定时器

创建时带 `timeout` 的沙箱会有一个过期定时器。服务器重启后，这些定时器需要重建——否则沙箱会永远活着。

Docker 后端的做法是从容器标签读回 `createdAt`，重新计算剩余的 TTL 并重建 timer。这看上去是个小细节，但它是「沙箱不在数据库里」这条不变量的直接成本：既然服务器不存沙箱列表，那就只能从运行时反查，而反查需要运行时上有可读的元数据。

于是容器标签成了半个数据库：

| 标签 | 用途 |
|---|---|
| `opensandbox.io/created-at` | 过期计算与响应一致性 |
| `opensandbox.io/embedding-proxy-port` | endpoint 解析 |
| `opensandbox.io/http-port` | 可选的直连 HTTP 映射 |
| `opensandbox.io/egress-sidecar-for` | sidecar 与主容器的关联 |
| `opensandbox.io/volume-managed-by` | 卷归属，避免误删别人的卷 |

这也解释了为什么 `metadata` 里有保留键校验：用户不能写 `opensandbox.io/` 前缀的键，否则会和这些内部标签冲突。

## 本章要点

- Docker 后端每个沙箱只映射一个宿主端口，其余端口通过 execd 的 `/proxy/{port}` 转发；HTTP/SSE/WebSocket 共用同一映射。
- `resolve_internal` 只在调用方确实能路由到容器网络时可用；容器化的服务器需要 `resolve_internal=false` + `host_ip`。
- `networkPolicy` 只在 `bridge` 模式生效，其他模式显式拒绝。
- execd 由运行时注入，`bootstrap` 负责启动顺序；这是镜像可定制的代价。
- 快照是本地镜像，含 rootfs 不含内存。
- 容器标签承担了「半个数据库」的角色，包括过期重建与 endpoint 解析。

## 本章源码入口

| 路径 | 看什么 |
|---|---|
| `server/opensandbox_server/services/docker/docker_service.py` | 生命周期主流程 |
| `server/opensandbox_server/services/docker/networking.py` | 网络模式校验、sidecar 关联、egress 只在 bridge |
| `server/opensandbox_server/services/docker/port_allocator.py` | 宿主端口分配 |
| `server/opensandbox_server/services/docker/metadata.py` | 保留标签前缀 |
| `server/opensandbox_server/services/docker/snapshot_runtime.py` | commit 成本地镜像 |
| `docs/architecture/network/single-host-network.md` | 单机路由模型与配置陷阱 |
