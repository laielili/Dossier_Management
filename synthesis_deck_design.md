# synthesis_deck 设计文档 — SVG 组件 → 自动排版

> 状态：已实现阶段 0–3（2026-08-16）。`src/svg2ppt/` 已落地（含前端页面 `static/svg2ppt.html` + 后端路由 `POST /svg2ppt/build`）。现有 `/html2pptx` 前后端未动。

## 1. 背景与根因

- `@summarize` 当前让下游 AI 直接吐**整段 HTML**（`.h-ppt-page` 1000×562.5，`position:absolute` 分区、内联样式、flex/grid、竖排文字等）。
- `html-to-pptx`（浏览器内运行）靠 `getComputedStyle` / `getBoundingClientRect` **遍历真实 DOM 几何**还原 PPTX。CSS 渲染几何 ≠ PPTX 形状几何 → 重叠、错位、竖排/嵌套定位丢失、字体度量偏差——格式错误源于此。
- 关键约束：`html-to-pptx` 必须在浏览器跑（纯 Python 无布局引擎），链路绕不开浏览器 + DOM 遍历。

新方向打在根因上：**让 AI 只产出自带确定几何的"内容 SVG 组件"，由本项目做排版/分页/装饰（chrome），彻底绕开"CSS→PPTX 几何误读"整类错误。**

## 2. 方案架构（设计草案，非代码）

新增独立 Python 模块（后端文件夹 `src/svg2ppt/`，不碰现有 `html2pptx`）：

- `schema` — 输入契约（XML/JSON）
- `layout` — 排版引擎：按模板把组件装进页、分区、防重叠、分页/续页
- `render` — 每页 SVG 表面 → 交付物（PPTX + HTML 预览）
- `templates` — deck 模板数据（以 `prompt/system.md` 5-region 为准）
- `api` — 增量端点 `POST /svg2ppt/build`（server 端排版，返回 PPTX/preview 下载 URL）+ 新 UI 页 `static/svg2ppt.html`（首页 `/` 用按钮导航）

输入契约草图（AI 给内容，不给坐标）：

```xml
<deck project="P-TIOX" theme="loreal">
  <component type="heading"     text="..."/>
  <component type="table"       .../>
  <component type="metric-card" .../>
  <component type="image"       src="logo.svg"/>
  <component type="text-block"  .../>
</deck>
```

注意：`type` 仅作 region 路由提示；AI **绝不**写 x/y/分区，全部空间决策由项目排版引擎负责。

## 3. 评估表：方案灵活性 + 用户可配置项

| # | 维度 | 可配置项 | 取值 / 选项 | 灵活性 | 默认建议 | 风险 / 备注 |
|---|------|----------|-------------|--------|----------|-------------|
| 1 | 产出页数 | **deck 页数（滑轮）** | 自动(内容驱动) / 固定 N / 软上限 N | 中 | 自动 + 软上限 | 无法绝对保证精确 N，除非缩放或裁剪内容 |
| 2 | 分页模式 | 组件→页的分配 | 模板固定每节 / 自动流式填充 / 手动拖拽 | 高 | 模板固定 + 溢出续页 | 与 system.md continuation-page 规则一致 |
| 3 | 组件模型 | AI 产出物形态 | 内容 SVG（type 仅路由）/ 透明 SVG 盒 / 全开放 | 高 | **内容 SVG + type 路由** | AI 不排坐标；越开放确定性越弱 |
| 4 | Deck 模板 | 段落结构 | system.md 5-region（权威）/ 自定义 | 中 | 5-region 固定 | 以 system.md 为准，旧"6 段"作废 |
| 5 | 渲染后端 | SVG→PPTX 方式 | Python 本地栅格(fitz→PNG→python-pptx) / 浏览器 pptxgenjs(矢量感) | 中 | **Python 栅格** | 栅格=离线稳但非可编辑矢量；矢量=需浏览器 |
| 6 | 输出格式 | 交付物 | PPTX / HTML 预览 / 两者 | 高 | 两者 | HTML 预览用于人工校验 |
| 7 | 主题 | 配色 / 字体 | 预设(L'Oréal 调色板) / 上传 JSON 主题 | 中 | 预设 | 取自 system.md 调色板 |
| 8 | 画布 | 尺寸 | 1000×562.5 (16:9) / 可选 | 低 | 固定 16:9 | 统一比例避免畸变 |
| 9 | 每节页预算 | 节级页数 | 自动 / 指定 | 中 | 自动 | 与全局页数滑块联动 |
| 10 | 溢出处理 | 内容超页 | 增加页(保真) / 缩放适应(降保真) | 中 | 增加页 | 决定滑块能否"压实"页数 |
| 11 | 几何校验 | 防重叠 / 越界 | 强制 / 关闭 | — | **强制** | 直击当前痛点，建议不可关 |
| 12 | 资产体积 | 图片 DPI / 最大体积 | 可调 | 中 | 受 **50MB** 下游限制 | 栅格分辨率直接影响文件大小 |

**方案灵活性总结**
- ✅ 几何确定性：每个 SVG 组件自带 `viewBox`/`w×h`，排版只是"把已知矩形放进已知页"——无 DOM 遍历、无 CSS 误读，格式错误类结构性消除。
- ✅ 关注点分离：AI 只管"内容+组件"，排版/主题/分页由项目负责 → AI 失败变成可测试、可复现的布局引擎问题。
- ✅ 可扩展：新组件=加一个渲染器；新模板=加一份数据；布局引擎不动。
- ✅ 离线：Python 栅格路径不依赖浏览器、无 headless。
- ⚠️ 残余风险：SVG→PPTX 仍需渲染后端；选栅格则交付 PPTX 为图片型（不可在 PowerPoint 内编辑形状），换取 100% 视觉保真 + 离线。
- ⚠️ 页数滑块是软控制：内容必然可强制超页，精确 N 只能靠缩放/裁剪换（保真降级）。

## 4. 已确认决策（用户答复）

- **决策①（模板）**：deck 模板以 `prompt/system.md` 为准——每页 5-region（top_banner / meta_row / left_column / middle_column / right_column，画布 1000×562.5），含 `field_to_region_mapping` 与 continuation-page 规则（续页复刻 top_banner/meta_row/left/right，仅 middle 续排）。旧记忆中"6 段"口径作废。
- **决策②（组件模型）**：AI **不考虑排版**，仅产出"带内容的 SVG 组件"。项目独占 100% 布局（含 region 分配、分页、加 banner/side-tab/节标题等 chrome）。为让排版引擎把组件路由到正确 region，AI 组件可带语义 `type` 标签作放置提示——但 type 仅用于放置，AI 仍不排坐标。
- **决策③（渲染后端）**：Python 本地栅格。组件 SVG → PyMuPDF(fitz) PNG → python-pptx 按矩形放入 16:9 幻灯片；离线、无浏览器；交付为图片型 PPTX（不可编辑形状），换取保真 + 确定性。
- **决策④（页数滑块语义）**：滑块 = 软上限 max_pages；默认溢出 = 加页保真（续页复刻 chrome 仅 middle 续排）；另提供 scale-to-fit 模式（缩放压实到上限，降保真）。AI 不控制页数。

## 5. 已生成文件

| 文件 | 说明 |
|------|------|
| `src/svg2ppt/__init__.py` | 高层入口 `DeckBuilder`：XML → PPTX/HTML |
| `src/svg2ppt/schema.py` | XML 解析、组件 dataclass、SVG 几何读取 |
| `src/svg2ppt/layout.py` | 5-region 路由、纵向堆叠、续页/分页、max_pages 软上限 |
| `src/svg2ppt/render.py` | fitz SVG→PNG + Pillow 合成 + python-pptx 组装 |
| `src/svg2ppt/api.py` | FastAPI 路由（`GET /svg2ppt`、`POST /svg2ppt/build`、`GET /svg2ppt/files/...`） |
| `src/svg2ppt/templates/deck_5region.json` | system.md 5-region 模板数据（坐标/配色/路由/续页规则） |
| `requirements.txt` | 新增 `python-pptx` |

## 6. 快速使用

```python
from svg2ppt import DeckBuilder

builder = DeckBuilder(template_path=None, dpi=150)
result = builder.build_from_file(
    xml_path="output/my_deck_input/deck.xml",
    output_dir="output/my_deck",
)
print(result.pptx_path)     # 交付 PPTX
print(result.preview_path)  # HTML 预览
print(result.page_count)    # 实际页数
```

## 7. 待确认 / 后续

- 阶段 3（已实现 2026-08-16）：API/UI — `POST /svg2ppt/build` + 新页面 `static/svg2ppt.html`（首页 `/` 加按钮导航）。

- 阶段 0：定 XML schema + 5-region 模板数据（来自 system.md）
- 阶段 1：layout 引擎（region 路由 + 防重叠 + 分页/续页）
- 阶段 2：render 后端（先 Python 栅格跑通）
- 阶段 3：api + 新 UI 页
