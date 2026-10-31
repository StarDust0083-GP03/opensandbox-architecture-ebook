# OpenSandbox 架构解剖：从一次创建请求到沙箱里的进程

基于 `opensandbox-group/OpenSandbox` 提交 `c2f55b20e945d89efe3d35220942a997a3551751`（2026-10-30）的中文架构电子书。沿着 `POST /v1/sandboxes` 走一遍：控制面、三种运行时后端、沙箱内的 execd 数据面、入向与出向网络、暂停与快照、多租户、隔离、可观测性，最后算一遍这套设计放弃了什么。

20 篇正文 + 3 份附录，每章附源码入口表。全部内容基于静态阅读源码、OpenAPI 契约与官方文档；**没有部署这套系统，没有做性能测试或安全审计**，所有性能数字都标注了出处与适用条件。

## 在线阅读

![pages](https://github.com/StarDust0083-GP03/opensandbox-architecture-ebook/actions/workflows/pages.yml/badge.svg)

- 阅读站：<https://StarDust0083-GP03.github.io/opensandbox-architecture-ebook/>
- `wiki/index.html`：分章目录、全文搜索、字号调整
- `wiki/book.html`：单文件离线合订本，无 CDN、无运行时网络请求
- `exports/opensandbox-architecture.epub`：EPUB 3（构建时同时复制到 `wiki/` 供阅读站下载）
- `book/OpenSandbox架构解剖.md`：合并书稿
- `book/SUMMARY.md`：分章目录
- `sources/inventory.json`：79 个固定源码/文档路径及 SHA-256

## 内容结构

| 篇章 | 内容 |
| --- | --- |
| 00 至 02 | 前言、六条需求、一条契约 / 三种后端 / 两个平面 |
| 03 至 05 | 四份 OpenAPI 与它们没写的东西、控制面四条不变量、运行时路由 |
| 06 至 08 | Docker 单端口路由、Kubernetes BatchSandbox 与 Pool、Fast Sandbox 与 Firecracker |
| 09 | execd：命令、文件、PTY、隔离会话与流式语义 |
| 10 至 12 | endpoint 与 ingress、egress 两层执行、Credential Vault |
| 13 至 15 | 暂停/恢复/快照/过期、身份与多租户、隔离与加固 |
| 16 至 19 | 可观测性、运维与失败、十项权衡、一次请求走完全书 |
| 附录 | 源码阅读地图、来源与事实边界、配套教学实现 |

正文区分**源码观察 / 文档陈述 / 教学推演 / 教学场景**四种内容，前言里有约定。

## 运行教学实现

Python 3.10+，只用标准库。它复刻了本书讨论的结构：`SandboxService` 接口、`docker`/`kubernetes` 工厂、`fsb-` 前缀分发、单主机端口 + `/proxy/{port}`、Pool 容量与 429、SSE 事件与退出码、egress 规则优先级、绝对 TTL 与暂停/恢复。

```bash
python3 examples/mini_osb.py
python3 -m unittest discover -s examples -v
```

22 项离线测试覆盖上述语义。它不是 OpenSandbox 的替代实现，也不连接任何真实沙箱。

## 重建阅读版

需要 Node.js 22 以上（构建）与 Python 3.10 以上（校验、EPUB 打包）。

```bash
npm ci --prefix wiki --ignore-scripts
npm run build --prefix wiki
python3 wiki/verify.py
```

构建会更新分章页面、搜索页、单文件 HTML、合并书稿、附录 C（从 `examples/` 同步）与 EPUB。`verify.py` 检查内部链接、锚点、XML、资源清单、EPUB 阅读顺序与来源清单，不等于通过官方 epubcheck。

本地预览：

```bash
python3 -m http.server 8000 --bind 127.0.0.1
# 打开 http://127.0.0.1:8000/wiki/
```

## 来源与许可

上游仓库固定在上表提交；研究副本位于 `sources/repos/OpenSandbox`（不进入仓库、不参与构建）。上游代码是 Apache-2.0，文档是 CC BY 4.0（CHARTER 第 7.b.iv 条）。本书正文与教学实现独立撰写，引用处给出路径与出处，不整段搬运上游文档。

书稿与代码的许可见 [LICENSE](LICENSE)。性能数字全部来自上游自测，本书未复现；`docs/guides/pause-resume.md` 等章节里的中断与不保证事项在正文中原样保留。
