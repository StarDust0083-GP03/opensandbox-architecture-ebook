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

## 图表检查

三张图都是矢量图：整体架构为手写 SVG，时序图与状态机为 Mermaid（`book/diagrams/*.mmd`）渲染的 SVG。构建时进入站点（`<img>`）、单文件（内联 SVG）与 EPUB（独立 SVG 资源 + manifest）。

| 图 | 画布 | 节点 | 文本 | 几何问题 |
|---|---|---:|---:|---:|
| `architecture.svg` | 1040×908 | 17 | 68 | 0 |
| `request-flow.svg` | 784×1088 | 0（时序） | 32 | 0 |
| `lifecycle.svg` | 703×791 | 8 | 21 | 0 |

检查脚本 `book/diagrams/checks/geometry.js`，把 SVG 内联进空白页后用 `getBoundingClientRect()`（含 transform）逐元素断言：

- 每个 `<text>` 的屏幕矩形都落在画布内 → 无裁切；
- 每个节点内的文字落在自己的矩形内 → 无 `label-outside-node`；
- 节点矩形两两不重叠；
- Mermaid 边标签不压住节点；
- 时序图退化检查：文字两两重叠面积不超过较小者的 25%。

修复记录（由检查发现）：

1. 第一版架构图用 ASCII 伪图，已废弃；改为 SVG。
2. Mermaid 架构图初版渲染成 526×2178（`direction` 在跨子图连线下被忽略），改为手写 SVG 精确布局。
3. 时序图初版 1348 宽，且有 3 处压字：`autonumber` 序号压在消息文字上（74% 面积）、参与者名「生命周期服务器」折行导致两行文字重叠。处理：去掉 `autonumber`、参与者改「控制面」、收窄到 784 宽。
4. 图片曾用 `loading="lazy"`，导致首屏外图不适配宽度、布局跳动；改为输出 `width`/`height` 属性。
5. EPUB 的 `book.css` 原先没有 `img` 规则，1040px 宽的图会溢出页面；补 `img{max-width:100%;height:auto}`。
6. 站点的图在深色模式下会露出深色 padding，改为图版固定纸色（图只有浅色版）。

页面级检查（重建后）：

- 桌面 780px 内容宽度：三张图均加载成功（`complete` 且 `naturalWidth>0`），宽高比与 viewBox 一致（780×684 / 780×1074 / 780×875）。
- 375px 视口（iframe 模拟，媒体查询生效）：页面横向溢出为 0；图在 `figure` 内横向滚动，`img` 最小 640px，正文不被挤压。
- EPUB：三个 SVG 作为独立资源打包，`02-architecture.xhtml` / `13-lifecycle-state.xhtml` 的 `<img src>` 指向它们，`verify.py` 的资源可达性检查通过。

**没做到的**：本轮会话里没有可用的人眼或视觉模型复核（图片输入不被当前模型支持，可用的 classifier 也拒绝图像输入）。因此上述是几何与结构检查，不等于「好看」。`book/diagrams/preview/*.png` 是从渲染页截的图，供人工过目；发现观感问题可直接改 `book/diagrams/` 下的源文件重渲染。

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
