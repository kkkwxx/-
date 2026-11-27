# UI Design System

本文件是本项目前端视觉与组件选型的**唯一规范来源**。新增/修改任何界面之前先读这里；与本文件冲突的实现视为缺陷。

> 状态：v3（配色锁定为配色板 5 色「青柠绿 + 深墨绿 + 中灰 + 近白 + 画布灰」，并规定混搭配方）。第 6 节记录「规范目标栈」与「当前仓库实际栈」的差异，迁移前请按第 6 节的落地方式执行。

## 1. Technology Stack

规范目标栈：

- React + TypeScript
- Tailwind CSS
- shadcn/ui
- Motion for React
- SVG for custom data visualization

## 2. Component Selection Rules

优先从以下资源库寻找现成组件：

1. **shadcn/ui**：基础组件和交互组件
2. **Magic UI**：动态效果、渐变、动画卡片
3. **Aceternity UI**：3D、玻璃质感、光效
4. **Spectrum UI**：综合视觉组件
5. **Kibo UI**：复杂功能型组件
6. **React Bits**：动态交互和背景效果

**禁止在没有检查现有组件库的情况下重复开发相同功能。**

检索方式（本机已配置 shadcn MCP，工具名以 `shadcn` 前缀）：

| 目的 | MCP 工具 |
|---|---|
| 列出可用 registry | `get_project_registries` |
| 在某 registry 内搜索 | `search_items_in_registries` |
| 查看组件详情/源码 | `view_items_in_registries` |
| 取示例代码 | `get_item_examples_from_registries` |
| 生成安装命令 | `get_add_command_for_items` |
| 安装前检查清单 | `get_audit_checklist` |

## 3. Design Principles

- 采用**统一的设计 Token**，包括颜色、字体、圆角、阴影、间距。
- 优先使用精致、现代、具有层次感的视觉设计。
- 避免默认浏览器样式和未经设计的基础 HTML 组件。
- **避免所有卡片使用相同的纯色背景和边框。**
- 合理使用渐变、玻璃质感、阴影、微交互和动画。
- 动画必须**服务于信息展示**，不要过度装饰。
- 保持整个项目的视觉一致性。

## 4. Development Workflow

1. 开发前先搜索现有 Registry。
2. 优先复用成熟组件及其源码。
3. 检查组件依赖、Props 和适配情况。
4. 结合业务数据进行二次封装。
5. 统一主题和样式，**不允许不同组件各自定义一套视觉规范**。
6. 对复杂图表优先考虑 **SVG 自定义绘制**。

## 5. Quality Requirements

- 组件必须响应式。
- 交互必须真实可用。
- 不允许用静态图片代替实际业务组件。
- 不允许为了视觉效果牺牲数据准确性。
- 保证 TypeScript 类型完整。
- 保证组件能够复用和维护。

---

## 6. 当前仓库实际栈与落地方式

### 6.1 现状（与本规范的差异）

| 规范目标 | 当前实际 | 说明 |
|---|---|---|
| Tailwind CSS | **未引入** | 样式集中在 `frontend/src/styles/admin.css`（后台设计层）与 `column.css`（公开栏目页） |
| shadcn/ui | **未引入** | 后台 UI 基础组件来自 **AntD 5**，通过 `frontend/src/theme.ts` 统一主题 |
| Motion for React | **未引入** | 动画用 CSS transition / `@keyframes` |
| SVG 自定义图表 | **已采用** | `frontend/src/components/VerticalColumnChart.tsx`（零依赖 SVG 竖向柱状图） |
| 设计 Token | **已建立** | AntD token（`theme.ts`）+ CSS 变量 `--mn-*`（`admin.css`） |

结论：**视觉规范（第 3、5 节）与选型流程（第 2、4 节）立即生效**；技术栈（第 1 节）尚未迁移，需要单独立项（引入 Tailwind 会与 AntD 主题并存，需先评估）。

### 6.2 迁移前的等效执行方式

- 需要「shadcn 风格」组件时：先用 shadcn MCP 检索 → 取源码/示例 → **按本仓库 Token 手工移植**为 React 组件，禁止直接 npm 安装 shadcn 运行时依赖。
- 基础交互组件（按钮、表格、弹窗、表单）：**优先用 AntD 而非重写**，统一由 `theme.ts` 的组件级 token 定制。
- 图表、特殊可视化：SVG 自绘，作为可复用组件放在 `frontend/src/components/`，数据全部由 props 驱动。
- 不允许在页面里写死颜色/圆角/阴影，必须使用 `--mn-*` 变量或 AntD token。

### 6.3 现有设计 Token（权威值，改色只改这两处）

定义位置：`frontend/src/styles/admin.css`（CSS 变量）与 `frontend/src/theme.ts`（AntD `ThemeConfig`），两者必须同步。

**基础色板（唯一来源，只允许这 5 色）**

来源：配色板逐像素实测。**整个后台只允许这 5 个色相**，除此之外的颜色必须由这 5 色"加白 / 加墨"派生，不得引入新色相（含蓝、紫、橙、红系，语义色除外）。

| Token | 值 | 角色 | 落点 |
|---|---|---|---|
| `--mn-lime` | `#AEE680` | 青柠绿 | 强调**实底**：主按钮、选中胶囊、图标 chip、图表条、徽标 |
| `--mn-ink` | `#122E28` | 深墨绿 | 主文字 + 深色块（深色卡）+ **侧栏选中项的文字/图标** |
| `--mn-gray` | `#7A7A7A` | 中灰 | 次要文字、图标、占位、禁用 |
| `--mn-white` | `#FEFEFE` | 近白 | 卡片面、表体、输入框、弹层 |
| `--mn-canvas` | `#E6E6E6` | 画布灰 | 外壳底、分隔线、空态底、进度轨道 |

**派生色（只许由上面 5 色加减白/墨）**

| Token | 值 | 派生公式 |
|---|---|---|
| `--mn-lime-soft` | `#EEF9E5` | 绿 20% + 白 80%（浅绿底） |
| `--mn-accent-hover` | `#97CA73` | 绿 85% + 墨 15% |
| `--mn-lime-deep` | `#7FAF66` | 绿 70% + 墨 30%（图表深端） |
| `--mn-primary` | `#3D6B12` | 绿压暗到白底可读（6.3:1）→ 绿色文字/链接 |
| `--mn-ink-soft` | `#1D3A33` | 墨 + 少量绿（深块渐变亮端） |
| `--mn-text-secondary` | `#4D625E` | 墨 75% + 白 25%（6.5:1） |
| `--mn-gray-1/2/3` | `#ECECEC` / `#E0E0E0` / `#CCCCCC` | 画布灰 → 近白之间的三档 |
| `--mn-surface-muted` | `#F2F2F2` | 画布灰 50% + 白 50% |

**语义别名（页面只用这一层，禁止写字面色值）**

| Token | 指向 | 用途 |
|---|---|---|
| `--mn-shell` | `--mn-canvas` | 最外壳背景 |
| `--mn-sider-bg` | `#F0F0F0` | 侧栏盒底（比外壳 `#E6E6E6` 浅一档） |
| `--mn-sider-hover` | `#F7F7F7` | 侧栏未选中项 hover（必须弱于选中态） |
| `--mn-sider-selected-bg` | `--mn-white` | 侧栏选中项：**纯白卡** |
| `--mn-sider-selected-ink` | `--mn-ink` | 侧栏选中项：**深绿字 + 同色图标** |
| `--mn-sider-selected-border` | `--mn-gray-2` | 侧栏选中项描边（用 box-shadow 0 0 0 1px 实现，避免挤动布局） |
| `--mn-card-border` | `--mn-gray-2` | 卡片/侧栏盒描边 |
| `--mn-border` / `-subtle` / `-strong` | `gray-2` / `gray-1` / `gray-3` | 描边三档 |
| `--mn-text` / `-secondary` / `-tertiary` | `ink` / `ink-2` / `gray` | 文字三档 |
| `--mn-accent` / `-soft` / `-hover` / `-ink` | `lime` / `lime-soft` / `lime-hover` / `ink` | 绿四件套 |
| `--mn-dark-surface` / `--mn-dark-gradient` / `--mn-dark-text` / `--mn-dark-muted` | `ink` 系 | 深色块（墨绿实底卡） |

**两条硬规则**

1. **绿底不做绿字**：`#AEE680` 之上文字/图标一律 `--mn-accent-ink`（`#122E28`，9.97:1）；绿底严禁白字（1.9:1）。
2. **深块不做灰字**：`#122E28` 之上文字用近白（14.4:1），弱化文字用青柠绿（9.97:1），严禁 `#7A7A7A`（1.5:1）。

### 6.3.1 混搭配方（每屏必守）

「混搭」= **同一套 5 色，用不同的"面 / 线 / 深块"组合出层次**，而不是给每张卡换一个色相。

| 角色 | 配方 | 面积配额 |
|---|---|---|
| **面**（安静） | 近白底 + `gray-2` 1px 描边 + 墨绿字 | ≥ 50% |
| **面**（着色） | `lime-soft` 底 + **无描边** + 墨绿字 | ≤ 20% |
| **线**（轻强调） | 近白底 + 左侧 3px 青柠绿竖条 | 点缀 |
| **深块**（重强调） | `ink` 实底（可渐变到 `ink-soft`）+ 近白字 + 绿 chip | **每屏最多 1 处** |

落地示例（总览页 4 张 KPI 卡，`mn-kpi--*`）：

| 卡 | 面 | 描边 | chip |
|---|---|---|---|
| A `--blue` | 近白 | `gray-2` 1px | `surface-muted` + 墨绿图标 |
| B `--amber` | `lime-soft` | 无 | 近白 + 深绿图标 |
| C `--green` | 近白 + 左 3px 绿条 | 其余三边 `gray-2` | `gray-1` + 次文字色图标 |
| D `--slate` | **`ink` 渐变** | `ink-soft` | 青柠绿 + 墨绿图标 |

**禁止**：4 张卡同款；一屏出现 2 个以上深块；把 5 色平均分配（灰阶必须占绝对多数，绿只做点缀）。

**侧栏选中项（独立配方，参考图样式）**：不占"深块"配额。

| 状态 | 面 | 字 | 图标 |
|---|---|---|---|
| 选中 | **纯白 `#FEFEFE`** + `#E0E0E0` 1px 描边（box-shadow 实现）+ 极淡投影 | 深绿 `#122E28`，600 字重 | **与文字同色**（单色深绿，不掺亮绿） |
| hover | `#F7F7F7`（只比栏底 `#F0F0F0` 亮一档） | `--mn-text` | 继承 |
| 默认 | 透明（露出栏底） | `--mn-text-secondary` | 继承 |

> 为什么不用深色块做选中：侧栏是常驻区域，深块会持续抢焦点；选中态改用"白卡浮起 + 深绿字"，把**深块额度留给内容区**（每屏 1 处）。

**形状与阴影**

| Token | 值 |
|---|---|
| `--mn-radius` / `-sm` / `-xs` | `12px` / `8px` / `6px` |
| 卡片阴影 | `0 1px 2px rgba(18,46,40,.06)`（无重投影，色相取墨绿 `#122E28` 的透明版） |
| 浮层阴影 | `0 10px 26px rgba(18,46,40,.12)` |
| 深块阴影 | `0 4px 14px rgba(18,46,40,.18)`（仅 `--slate` 深色卡用） |

**字体**：系统字体栈（`-apple-system` / `PingFang SC` / `Microsoft YaHei` / `Segoe UI`），基础字号 `13px`，表格 `12–13px`，标题 `14–16px`。

**布局骨架**：外壳 `padding/gap 14px` + 侧栏盒 `18px` 圆角浮动 + 内容区独立滚动；侧栏宽 `272px`，顶栏高 `56px`。

### 6.4 图表专项约定

当前唯一图表：`frontend/src/components/VerticalColumnChart.tsx`（总览页「五大栏目分布」）。

- 一律 **SVG 自绘 + props 驱动 + `ResizeObserver` 自适应宽度**，不引入图表库。
- **竖向柱状图**（参考图样式）：柱宽 = 节距 × 0.62（上限 64px），仅柱顶两角圆角 `r=6`，柱底贴基线。
- **渐变**：全图共用一条 `userSpaceOnUse` 竖向渐变（`y1=绘图区顶` → `y2=基线`）：`#aee680` → `#caf0ae`(45%) → `#e2f5cf`。柱越高越接近高饱和绿，柱底统一偏浅。**不允许**逐柱各自渐变（会失去"高度=浓度"的一致语义）。
- **选中/激活**：`#122e28 → #3a5a51` 深墨绿渐变 + 柱底 `--mn-accent-soft` 衬块 + 标签转深墨绿加粗。
- **无网格线、无坐标轴**：数值直接印在柱下（`数值 · 占比`），保证不依赖 Tooltip 也能读数。
- 柱下两行标签：栏目名（`12.5px`，次文字色）→ `数值 + 占比`（`12px` 加粗 / `11px` 中灰）；宽度 < 520px 时隐藏占比避免拥挤。
- 动画：柱体从基线向上生长（`scaleY`，`0.55s`，逐柱延迟 `50ms`），服务于"量级对比"。
- 必须提供 hover Tooltip、点击选中、键盘可聚焦（`Enter/Space` 选中），数值与比例严格来自接口数据，不得为视觉效果调整数值。
- 图表内**不得**出现与主色板无关的色相；渐变只能走青柠绿 → 浅绿 与 深墨绿 → 墨绿灰 这两条谱系。

### 6.5 例外与豁免

公开栏目页（`/column`）是独立的深色移动端风格，使用 `frontend/src/styles/column.css` 与其自有变量，**不受后台 Token 约束**，但同样遵守第 3、5 节原则。注意：该文件在 `:root` 定义了与后台同名前缀的变量，因此后台变量必须作用在 `.mn-admin/.mn-login/.mn-boot` 容器上（不要写回 `:root`）。

**数据编码色豁免**：`frontend/src/utils/labels.ts` 里的 `EVENT_TYPE_META`（事件类型 6 类）等分类色是**数据编码**，用于区分维度而非装饰，且与公开栏目页共用同一份常量，因此**不纳入 5 色约束**。使用规则：

- 只允许出现在 `Tag` 小色点 / 小徽标 / 图例圆点上，**禁止**用于卡片背景、大面积填充、按钮底色。
- 新增分类色时必须保证在近白底上可读（≥ 4.5:1）；不满足时改用「墨绿字 + 绿点」的组合。
- 语义色（`--mn-success / -warning / -danger`）同理：只用于状态标签与危险操作，不做装饰。
