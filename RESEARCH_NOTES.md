# 编写与核对记录

## 目标

用户给出 `https://github.com/opensandbox-group/OpenSandbox`，要求参考本机已有 ebook 写一本电子书并上传到 GitHub。因此沿用本机 `octop-architecture-ebook`（架构解剖体例）与 `rag-techniques-from-scratch` / `langchain-langgraph-from-scratch`（书稿 + 阅读站 + EPUB 构建流水线）的结构，而不是从零发明目录。

## 上游固定

- 仓库：`https://github.com/opensandbox-group/OpenSandbox`
- 提交：`c2f55b20e945d89efe3d35220942a997a3551751`，2026-10-30 23:09:43 +0800
- 方法：`git clone --depth 1`，静态阅读
- 核对日期：2026-10-31

## 章节如何定下来

先读 `docs/architecture/index.md` 拿到六个 surface 与三条交互路径，再读四个后端的架构页（server / operator / execd / fast-sandbox）确定可以拆开的边界。章节顺序据此定为「问题 → 方案 → 契约 → 控制面 → 后端 → 数据面 → 网络 → 状态 → 组织 → 权衡 → 串联」，与 octop 的组织方式一致，但边界来自 OpenSandbox 自己的分层文档。

20 篇正文的取舍：

- 把「入向网络」「出向网络」「凭证」分成三篇，因为它们在代码里分属 ingress、egress、mitmproxy 三个子系统，且失败模式完全不同。
- 把「暂停/恢复/快照/过期」合成一篇，因为文档里这四者共享同一组状态机语义，拆开会让读者反复查状态表。
- 单独立「权衡」一篇（不放在完结篇里），因为上游自己公开了大量限制与未验证项，值得集中算账。
- 没有单独的 CLI / MCP 篇：它们只是契约为轴的客户端，放到附录 A 的路径表里。

## 技术来源（按篇）

- 控制面：`docs/architecture/control-plane/server.md` + `main.py` / `startup_guard.py` / `api/lifecycle.py` / `proxy.py` / `validators.py`。
- 路由：`services/sandbox_service.py`（13 个抽象方法）、`factory.py`、`composite_service.py`、`runtime_resolver.py`、`snapshot_restore.py`。
- Docker：`single-host-network.md` + `docker/networking.py`、`metadata.py`、`port_allocator.py`、`snapshot_runtime.py`。
- Kubernetes：`control-plane/operator.md` + `batchsandbox_types.go` / `pool_types.go` / `sandboxsnapshot_types.go` + `configuration.md` 的创建等待配置。
- Fast Sandbox：`fast-sandbox/` 九篇（index/templates/scheduling/networking/checkpoints/ha/storage/firecracker/performance）。
- 数据面：`data-plane/execd.md` + `command.go`、`codeinterpreting.go`、`isolation-sessions.md`。
- 入向：`network/ingress.md` + `proxy/header.go` + `secure-access.md`。
- 出向：`network/egress.md` + `policy.go`、`always_rules.go`。
- 凭证：`guides/credential-vault.md`（要求、五类 auth、替换、路径守卫、暂停语义、三个 TLS 旋钮）+ `oseps/0012`、`0022`。
- 状态：`guides/pause-resume.md`（持久性、单副本、Pod 终止窗口）+ `client-pool.md` + `integrations/idle_pause/`。
- 组织：`multi-tenancy.md`（六步流程、隔离模型、池非租户作用域）+ `middleware/auth.py`、`tenants/`。
- 隔离：`secure-container.md`（开销表、兼容矩阵）+ `network-isolation.md` + `oseps/0004`。
- 观测：`data-plane/node-agent.md` + `telemetry.md` + `api/devops.py` + `nodeagent/pkg/marker/`。
- 运维：`deployment/index.md`（部署顺序、单副本、PostgreSQL 拓扑）+ chart values + `release-verification.md`。

## 保留的「不确定」

上游文档里有三类内容被我刻意保留为「未验证」，没有替它下结论：

1. Fast Sandbox 并发 20 创建的成功率与原因（文档只给现象：48 次 429、1 次超时、4 个 CR 卡删除）；
2. 同节点恢复 62.66 秒与运行时恢复几十毫秒之间的差距（文档明说未定位）；
3. node agent 的资源默认值仍在等 benchmark/soak（OSEP-0019）。

第 17 篇把这三条单独成节，因为它们比任何功能描述都更能说明项目当前状态。

## 观察到的文档漂移

`appendix-sources.md` 里列了 6 条可核对的不一致（Go module 与 npm scope 仍用 `alibaba`、`/pty` 不在契约里、隔离会话 diff/commit 未实现、`server/v*` 冻结标签、`auto-pause-wake` 与 `auto-pause-wake-on-access` 两个相邻主题、`diagnostic-api.yml` 的 `.yml` 后缀）。写下来是为了让读者少踩坑，不是缺陷主张。

## 本书独立实现

`examples/mini_osb.py` 用标准库重写了本书讨论的结构：服务接口、工厂、前缀分发、单端口代理路径、池容量与耗尽、SSE 事件、egress 优先级、绝对 TTL、暂停/恢复。22 项 unittest 只验证这些结构性语义，不声称与上游行为一致。

## 未做

- 未部署、未运行上游服务与 e2e 测试。
- 未做性能测量；书中所有毫秒数均引自上游文档并标注条件。
- 未做安全审计；nftables、TLS 拦截、路径守卫的描述是「文档/代码这样实现」，不是「已验证无漏洞」。
- 未通读 24 份 OSEP；引用编号处依据文档与 ROADMAP 的转述。
- 未验证 README 中 30+ 集成示例的可运行性。

## 质量检查

- 逐章检查：结论是否能指到路径；数字是否带了适用条件；「不做/不支持/未验证」是否被保留。
- `node wiki/build.mjs`：23 个篇章页面、搜索页、单文件 HTML、合并书稿、EPUB。
- `python3 wiki/verify.py`：26 个 HTML 页面的链接与锚点、79 条来源清单、23 章 EPUB 结构。
- `python3 -m unittest discover -s examples -v`：22 项通过。
