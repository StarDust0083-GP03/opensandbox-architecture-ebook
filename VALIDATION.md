# 验证记录

## 本轮图表复查

范围：三张已发布的图、对应图注、网页/离线版/EPUB 的图像构建链路。方法：对照固定源码、读取浏览器渲染截图、检查 DOM 几何与资源引用。

### 发现并修复

| 问题 | 原因与修正 |
|---|---|
| 架构关系误导 | 原图把网络与观测放在同一条纵向链路。改成 8 个组件的关系图，分开管理请求、入向访问、出向控制与旁路观测。 |
| Fast Sandbox 进程边界错误 | 原图统一画作沙箱内 sidecar。明确容器用 sidecar、Fast Sandbox 每 Fastlet 一个 guest 外共享 egress 进程。 |
| 429 后继续走成功路径 | 原时序 alt 块后无条件接上启动与执行。只画 BatchSandbox 池化成功路径，单独说明容量耗尽请求在 429 处结束。 |
| 暂停失败被当成终态 | 对照 `server/.../k8s/batchsandbox_provider.py` 的 pause/resume 校验：`Succeed + PauseFailed` 与 `Paused + ResumeFailed` 可重试。图中区分条件与 Failed 状态。 |
| 删除等于正常退出 | 删除清理后 GET 为 404，不等于主进程退出码为 0。将删除语义移为独立说明，不画 `Stopping → Terminated` 假边。 |
| 自动布局返回边绕远、标签挤压 | 实际读取三张截图后，所有发布图改为手工正交 SVG；Mermaid 只作为时序/状态的语义源。 |
| 离线图 ID 损坏 | 原构建器对 SVG ID 做两轮不同前缀改写，却未同步 CSS、marker 与 ARIA 引用。改用自包含 SVG data URI，保持源字节不变。 |
| 检查遗漏 | 几何脚本原先没有文字两两重叠检查，边标签检查只识别完全包含。补充面积重叠与矩形相交检查；不再把旧检查结论当成视觉验收。 |
| 合并 Markdown 有占位符 | 将图指令转换为相对 SVG 图片链接，合并稿不再显示构建占位符。 |

### 当前图表结果

| 图 | viewBox | 节点 | 文本 | 几何告警 |
|---|---|---:|---:|---:|
| architecture.svg | 1160×980 | 8 | 35 | 0 |
| request-flow.svg | 1080×980 | 4 | 20 | 0 |
| lifecycle.svg | 1040×960 | 7 | 28 | 0 |

对每张最终图读取了浏览器截图，检查标签、连线方向、返回路径、字体与分组。截图更新到 `book/diagrams/preview/`。没有使用生成式图像：这些图的精确状态与连线需要可编辑 SVG，不宜让图像模型重新猜测。

几何脚本：`book/diagrams/checks/geometry.js`，使用包含 transform 的 `getBoundingClientRect()`。检查画布越界、节点内文字、节点相交、边标签相交、文字重叠面积超过较小者的 25%。几何检查是补充，不是视觉审阅的替代。

### 浏览器与分发

在同源 iframe 中以 **375px / 1280px** 实际视口检查：

- 第 02 篇：2 张图片全部加载成功，页面横向溢出为 0。
- 第 13 篇：1 张图片加载成功，页面横向溢出为 0。
- 离线合订本：3 张 data URI 图片全部加载成功，页面横向溢出为 0。
- 手机端仅图框内横向滚动；桌面图框无横向滚动。
- EPUB 内含三个独立 SVG 资源，XHTML 图片引用与 manifest 可达；CSS 有 `img{max-width:100%;height:auto}`。

## 构建与回归

已运行：

```bash
node wiki/build.mjs
python3 wiki/verify.py
python3 -m unittest discover -s examples -q
```

结果：23 个篇章生成；26 个 HTML 页面的内部链接与锚点通过；79 条来源记录通过；EPUB 的 XML、资源及 23 项阅读顺序通过；22 项教学示例测试通过。

新回归断言：离线 HTML 中的 data URI 解码后必须与三个源 SVG 的字节集合相同；实际元素的 `url(#...)` 引用必须指向存在的 SVG ID，防止箭头标记再被重命名破坏。也对 EPUB 中 SVG 做 XML 解析。

## 边界

本轮只审阅图表与其构建链路，不代表全书逐行技术审计。没有运行 OpenSandbox、做生产性能测量或安全审计；没有官方 epubcheck 或实际硬件阅读器兼容认证。教学测试仅验证本书示例，不验证上游服务。
