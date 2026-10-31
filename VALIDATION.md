# 验证记录

日期：2026-10-31。环境：本机 Python 3.x、Node.js 22（构建）。

## 已运行

- `node wiki/build.mjs`：生成 23 个篇章页面（20 正文 + 3 附录）、目录、搜索页、单文件 `wiki/book.html`、合并书稿、附录 C、EPUB。
- `python3 wiki/verify.py`：26 个 HTML 页面的内部链接与锚点通过；79 条来源清单（路径 + SHA-256 + 行数）通过；EPUB 的 XML、资源、导航与 23 项阅读顺序通过；原始外部来源链接未被构建器改写；正文无 `TODO/TBD` 占位。
- `python3 -m unittest discover -s examples -v`：22 项离线测试通过（生命周期、endpoint 形态、路由与池、过期、数据面与 egress）。
- `python3 examples/mini_osb.py`：示例输出正常（创建、SSE 事件、egress 判定、endpoint、暂停/恢复）。
- 中文标点检查：20 篇正文 + 3 附录 + README 通过（无 CJK 之间的半角标点、无双空格）。

## 浏览器实查

使用 agent-browser 会话，本地 `python3 -m http.server`（127.0.0.1:8765）与 `file://` 两种方式：

- 目录页 23 个篇章入口可见；桌面 1280×900 与 375×812 均无页面级横向溢出（`scrollWidth - clientWidth = 0`，抽查目录、第 8、17 篇）。
- 分章页有章内目录（第 11 篇 11 个二级标题）、代码块独立横向滚动，页面本体不溢出。
- 字号按钮生效（20px → 21px）。
- 单文件 `wiki/book.html`：23 个章节区块、外部脚本数量为 0、内联搜索「nftables」命中 10 章。
- 未观察到页面脚本错误。

## 未做与不能推出的结论

- **没有部署或运行 OpenSandbox。** 没有起服务器、没有创建真实沙箱、没有跑上游 e2e。
- **没有性能测量。** 书中的毫秒数与成功率全部引自上游文档，并标注适用条件（详见附录 B 的数字出处表）。
- **没有安全审计。** nftables 规则、TLS 拦截、路径守卫、多租户隔离的描述都是「文档/代码这样实现」，不是「已验证没有漏洞」。
- **没有通过官方 epubcheck。** 结构校验是自建的，不覆盖所有阅读器兼容性。
- **没有验证上游 README 里 30+ 集成示例**的可运行性。
- **没有通读 24 份 OSEP**，引用编号处依据文档与 ROADMAP 的转述。

## 已知的呈现层取舍

- 阅读站部署 `wiki/` 目录；EPUB 与合并 Markdown 通过站内链接与 GitHub 链接提供，构建时把 EPUB 复制进 `wiki/`，避免 GitHub Pages 上的相对路径失效。
- 单文件版把章节 section id 前缀化（`<slug>-<heading-id>`）以避免跨章重名锚点，因此站内跨章锚点只在单文件内有效。
