# 前端对接清单

未修改首页 app/page.tsx、landing 组件、globals.css、图片和字体；保留 demo-trip 的原数据与原组件分支。

| 文件 | 变化 |
|---|---|
| lib/api.ts（新增） | 四个API、超时/取消、错误统一处理、Profile→Trip适配、仅demo可调用的legacyFetch |
| next.config.ts | beforeFiles将/api/*转发到localhost:8000 |
| app/api/[...path]/route.ts → app/mock-api/[...path]/route.ts | 原Mock处理器移动，内容不变 |
| types/trip.ts | Trip.profile、Activity.coordinatesUnknown可选字段 |
| components/create/trip-creator.tsx | 提交建任务，防重复提交，错误显示，必要文案纠正 |
| components/planning/planning-progress.tsx | 原进度容器接真实轮询；移除新任务的酒店和交通确认依赖 |
| components/history/trip-history.tsx | 后端摘要列表加演示入口；复制/删除/导出提示后续开放 |
| components/trip/trip-workspace.tsx | 读取结果与加载失败状态；新档案编辑/重规划等提示后续开放；未知费用/天气不显示虚假数字；备选景点在现有总览卡内展开 |
| components/trip/activity-card.tsx | 未知费用显示待复核 |
| components/trip/trip-map.tsx | 新档案未知坐标保留地图区域但不画假点位，地点导航入口和待复核信息 |
| components/trip/trip-map-inner.tsx | demo请求统一使用API模块，不变地图逻辑 |
| components/guide/profile-guide.tsx（新增） | 在现有卡片/弹窗/折叠/四区视觉组件中呈现完整后端内容 |
| components/guide/local-guide.tsx | 新档案用profile，demo保持静态分支 |
| components/guide/preparation-sections.tsx | 新档案四区内容与本机勾选状态，demo保持原分支 |
| components/preparation/preparation-agent-page.tsx | 新档案直达获取结果，避免错误回退到另一份行程；重新生成提示后续开放 |
| app/trip/[tripId]/export/page.tsx | 新档案直接访问导出页时提示后续开放；保留 demo 的原导出页面 |

生成/阅读以外操作仅demo继续原演示行为。新档案不进行Mock同步、路线模拟重算、酒店搜索、分享或伪持久化。前端没有智谱key；原demo高德SDK的环境变量引用保持原样，不新增凭证值。

创建页问卷内容与上游 `references/first-use-intake.md` 及其问卷字段对齐，继续复用行迹原有 Ticket、Field、Segmented、网格与按钮样式。保留本项目必需的出发地、多城市和明确日期；采用同行关系、预算定位与总预算、节奏、兴趣、特别想去、明确避开、限制与照顾、其他说明。因七模块版删除购物，未引入上游问卷中的购物类兴趣。交通和住宿不再询问推荐条件，只提示已订信息可写入其他说明，未提供时保持待确认。
