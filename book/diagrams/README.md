# 图与检查记录

书里有三张图，全部是矢量图，构建时进站点、单文件与 EPUB。

| 图 | 源 | 渲染产物 | 用在 |
|---|---|---|---|
| 整体架构 | `architecture.svg`（手写） | `architecture.svg` | 第 02 篇 |
| 创建到执行时序 | `request-flow.mmd`（Mermaid） | `request-flow.svg` | 第 02 篇 |
| 生命周期状态机 | `lifecycle.mmd`（Mermaid） | `lifecycle.svg` | 第 13 篇 |

## 预览

![整体架构](architecture.svg)

![创建到执行时序](request-flow.svg)

![生命周期状态机](lifecycle.svg)

PNG 版预览在 `preview/`（视口截图，浅色纸面）。

## 重新渲染

```bash
cd book/diagrams
npx -y @mermaid-js/mermaid-cli@11 -i architecture.mmd -o architecture.svg -c mermaid.json -p puppeteer.json
```

（手写的 `architecture.svg` 不需要渲染；`mermaid.json` 固定字体与配色，`puppeteer.json` 指向本机 Chromium。）

## 检查了什么

`checks/geometry.js` 在浏览器里对内联 SVG 做几何断言，逐图运行结果见 `VALIDATION.md`：

- 所有 `<text>` 的屏幕矩形都在画布内（不被裁切）；
- 每个节点内的文字完整落在该节点矩形内（`label-outside-node` 为 0）；
- 节点矩形两两不重叠；
- Mermaid 的边标签（`g.edgeLabel`）不与节点矩形相交；
- 文字之间没有超过 25% 面积的重叠。

检查方法：把 SVG 内联进一个空白页面，用 `getBoundingClientRect()`（包含 transform）而不是 `getBBox()`（局部坐标，会误报），逐元素比较。

几何通过不等于"好看"。本轮没有可用的人眼/视觉模型复核，所以另存了 `preview/*.png`，供人工过目。
