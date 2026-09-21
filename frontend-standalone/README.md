# 行迹 Xingji · 前端（Next.js）

行迹的用户界面：一个"旅行手账"风格的 AI 行程规划工作台。用户在问卷里表达旅行想法，前端负责把偏好提交给后端、实时呈现 Pack 化生成进度，并把最终的结构化旅行档案渲染成**可阅读、可编辑、可重规划**的地图时间轴工作台。

本目录是独立可运行的 Next.js 应用：连接后端时使用真实 Agent；未配置后端时，内置的 `/trip/demo-trip` 仍可完整体验全部交互。

## 用户旅程

1. **首页 `/`**：旅行手账主题 Landing，10 个目的地的 3D 翻页动画（DOM 合成翻页效果，支持拖拽翻页、键盘切换、索引直达，并尊重系统减弱动效偏好与低性能设备）。
2. **创建行程 `/create`**：票券风格问卷——出发地与多城市串联（最多 8 站）、日期、同行人与关系、预算定位与总预算、节奏、14 类兴趣标签、特别想去 / 明确避开、限制与照顾、自然语言补充说明。草稿由 Zustand + localStorage 持久化，提交时防重复点击并展示表单错误。
3. **生成进度 `/planning`**：提交后轮询 `/api/build/{job}`，按 Pack 呈现当前阶段与百分比；任务暂停时可"继续生成"，失败时显示面向用户的错误文案，而不是原始堆栈。
4. **行程工作台 `/trip/[id]`**：核心页面。
   - 逐日 Tab + 高德互动地图（站点、路段、地点弹层）；无坐标的地点不画假点位，提供"在地图查看"的导航入口与待复核提示；
   - 活动卡片展示时间、建议停留时长、用餐角色与时间窗、费用复核状态、锁定 / 必留标记；
   - dnd-kit 拖拽排序、编辑停留时长与开始时间、锁定、切换单日 / 路段交通方式；
   - "添加地点 / 活动"：先按名称搜索高德真实 POI 候选，选定后生成日程 diff 预览，确认才提交；也支持添加休息、自由活动等非 POI 自定义活动；
   - 自然语言重规划：单日"重排这一天"与"调整全程"，后端自动识别影响范围（当天 / 跨天 / 整趟），跨天与整趟调整必须二次确认；支持撤销最近一次修改；
   - 工作台功能区提供六个入口：
     - **预计花费**：汇总总预算与预计花费；真实档案统一显示"费用待复核"，不编造价格；Demo 行程提供住宿 / 餐饮 / 交通 / 活动 / 购物 / 其他的分类预算调整与超支预警；
     - **行前准备**：进入行前页面，真实档案渲染后端生成的行前清单、确认事项、方言语言锦囊与贴士四区内容；
     - **调整交通边界**：管理抵达、跨城与离开时间；Demo 行程支持选择班次 / 自定义时间并预览对后续日程的影响；
     - **更换酒店**：Demo 行程支持实时推荐 / FlyAI 搜索 / 高德定位三种方式，选定酒店后分析每日首尾路线变化；
     - **当地特色体验**：真实档案渲染后端产出的体验模块（可选体验与推荐理由）；Demo 使用内置静态指南；
     - **餐饮指南**：真实档案渲染菜单指南、当地小吃、推荐餐厅与可靠连锁；Demo 使用内置静态指南；
   - 真实档案本轮聚焦"生成与阅读 + 局部重规划"：预算调整、跨城交通边界、酒店更换、分享与导出在真实档案中标记为"后续开放"，在 Demo 行程中保留完整交互演示。
5. **行前准备 `/trip/[id]/preparation`**：基于最终行程与同行人生成的行前清单、需提前确认事项、当地语言关键词与短语、旅行贴士，勾选状态保存在本机。
6. **导出 `/trip/[id]/export`**：Demo 行程支持把行程导出为图片 / PDF（html-to-image + jsPDF）。
7. **历史 `/trips`**：读取后端已完成行程列表；并保留 Demo 行程独立入口。
8. **分享 `/share/[shareId]`**：只读分享视图。

## 技术栈

- **Next.js 16（App Router）+ React 19 + TypeScript**：后端 `DestinationProfile` 有完整的 TypeScript 契约类型
- **Tailwind CSS**：手账 / 票券视觉体系集中在 `app/globals.css`
- **Zustand**：行程草稿与本地持久化（`store/`）
- **Motion**：页面与卡片动效，尊重 `prefers-reduced-motion`
- **dnd-kit**：日程拖拽排序
- **@amap/amap-jsapi-loader**：高德 JS API 2.0 互动地图
- **html-to-image + jsPDF**：客户端导出图片与 PDF
- 所有服务端集成都走同源 `/api/*` 代理，**前端不持有任何模型密钥、Web 服务密钥或高德安全密钥**

## 目录说明

```text
frontend-standalone/
├── app/
│   ├── page.tsx                  # 手账式首页
│   ├── create/ planning/         # 创建问卷 / 生成进度
│   ├── trip/                     # 工作台、行前准备、导出
│   ├── trips/ share/             # 历史 / 分享
│   └── mock-api/[...path]/route.ts   # Demo 行程专用本地 Mock
├── components/
│   ├── landing/ create/ planning/
│   ├── trip/                     # 工作台、地图、活动卡、重规划弹窗
│   ├── guide/ preparation/ export/ share/ history/
│   └── ui.tsx                    # Ticket / Field / Button / Modal 等通用组件
├── features/pace/                # 旅行节奏引擎（问卷与节奏映射）
├── features/weather/             # 天气影响说明
├── lib/api.ts                    # 后端 API 客户端 + Profile→Trip 适配 + 错误归一化
├── lib/mock-backend.ts           # Demo 行程数据与变更模拟
├── store/                        # Zustand store 与 localStorage 持久化
├── types/                        # Trip / API 契约类型
└── public/destinations/          # 首页目的地插画
```

## 与后端的契约

- 所有真实接口走同源相对路径 `/api/...`（建任务、轮询、取结果、行程列表、全部重规划与编辑接口），统一在 `lib/api.ts` 封装：20 秒常规超时（重规划类 120 秒）、AbortSignal 取消、422 错误指针解析、`REPLAN_*` 用户文案映射。
- 后端返回的 `DestinationProfile` 在 `toTrip()` 中适配为 UI 模型；未知费用显示"待复核"，未知坐标不显示假数字，未经核验的营业与票价信息不呈现为确定事实。
- `/trip/demo-trip` 的请求被 `legacyFetch()` 引导到独立的 `/mock-api/*`，与真实后端完全隔离；其他页面不允许回退到 Mock 数据。
- 开发环境通过 `next.config.ts` 的 rewrites 把 `/api/*` 与 `/amap-security/*` 转发到 `http://localhost:8000`；生产环境由服务端环境变量 `BACKEND_URL` 指定 Railway 地址，在 Vercel 侧转发，浏览器始终同源访问，无需 CORS。

## 环境变量

| 变量 | 填写位置 | 用途 |
|---|---|---|
| `NEXT_PUBLIC_AMAP_JS_KEY` | `.env.local` / Vercel | 高德 JS API Key，浏览器端可见，需在高德控制台配置域名白名单 |
| `BACKEND_URL` | 仅 Vercel（**不带** `NEXT_PUBLIC_` 前缀） | 生产环境后端地址，供 rewrites 转发；本地开发默认 `http://localhost:8000` |

高德安全密钥（`securityJsCode`）**不在前端配置**：地图组件把高德安全校验指向同源 `/amap-security/jscode`，经 Next rewrite 转发到后端，由后端持有安全密钥并完成官方推荐的代理换取流程。不配置 JS Key 时，地图自动降级为内置的手绘风格路线预览，其他功能不受影响。

## 本地开发

需要 Node.js 20 或更高版本。

```bash
npm install
npm run dev        # http://localhost:3000
```

```bash
npm run build              # 生产构建
npm start                  # 生产启动
npm run typecheck          # TypeScript 类型检查
npm run test:storage       # 本地持久化单测
npm run test:api-errors    # 错误文案单测
```

只看前端演示：不启动后端，直接访问 http://localhost:3000/trip/demo-trip 。

完整本地联调需要同时启动后端（见上级目录 `backend/README.md`），后端默认监听 8000 端口。

## 部署（Vercel）

1. 导入 GitHub 仓库后，**Root Directory 指向 `frontend-standalone`**，Framework Preset 会自动识别 Next.js，无需额外构建命令。
2. 在 Project Settings → Environment Variables 配置：
   - `NEXT_PUBLIC_AMAP_JS_KEY`：高德浏览器端 JS Key；
   - `BACKEND_URL`：Railway 后端地址，如 `https://xingji-backend.up.railway.app`。
3. 部署完成后，在高德控制台为该 JS Key 添加 Vercel 线上域名白名单。
4. 高德安全密钥、智谱、Serper 等服务端密钥只配置在 Railway，绝不配置到 Vercel 或写进前端代码。
