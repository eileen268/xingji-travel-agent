# 行迹 Xingji · 后端

Python 3.11 + FastAPI + Pydantic v2 + SQLite。负责旅行档案的生成、调度、核验与可编辑重规划；不含账号体系、通用搜索 API、航班 / 酒店推荐与后端 HTML。

## 快速启动

```bash
cd backend
python --version            # 需要 3.11.x
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
Copy-Item .env.example .env   # macOS/Linux: cp .env.example .env
uvicorn app.main:app --reload
```

在 `.env` 中填写 `ZHIPU_API_KEY`，或在启动终端设置同名环境变量（环境变量优先于 `.env`）。**不要把 Key 发到聊天中，不要放入前端，也不要使用 `NEXT_PUBLIC_` 前缀。** 不配置智谱 Key 时，杭州目的地自动使用手写离线档案降级；其他城市在故障时进入 paused 等待恢复，不伪造内容。

服务默认 http://127.0.0.1:8000 ，交互式 API 文档位于 `/docs`，机器可读契约位于 `/openapi.json`。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `ZHIPU_API_KEY` | 空 | 智谱 GLM Key；空值启用杭州离线降级 |
| `ZHIPU_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容接口地址 |
| `ZHIPU_MODEL` | `glm-4-plus` | 使用的模型 |
| `DATABASE_PATH` | `data/travel.db` | SQLite 路径，相对路径以 backend 为基准；Railway 上指向 Volume |
| `AMAP_API_KEY` / `AMAP_WEB_SERVICE_KEY` | 空 | 高德 Web 服务 Key（POI / 路线），仅后端读取，二选一即可，优先 `AMAP_API_KEY` |
| `AMAP_SECURITY_JS_CODE` | 空 | 高德 JS API 安全密钥，仅用于服务端代理接口，绝不下发浏览器 |
| `AMAP_BASE_URL` / `AMAP_POI_BASE_URL` | v3 / v5 | 高德 Web 服务地址（路线走 v3，POI 走 v5） |
| `AMAP_CONNECT_TIMEOUT_SECONDS` / `AMAP_READ_TIMEOUT_SECONDS` | 10 / 20 | 高德连接 / 读取超时 |
| `AMAP_CACHE_TTL_SECONDS` / `AMAP_ROUTE_CACHE_TTL_SECONDS` | 2592000 / 604800 | POI 与路线缓存秒数 |
| `AMAP_ROUTE_FALLBACK_ENABLED` | 1 | 路线服务失败时按坐标距离进行保守估算 |
| `SERPER_API_KEY` | 空 | Serper Key，仅用于官网 URL 发现，不作为事实来源 |
| `SERPER_BASE_URL` / `SERPER_TIMEOUT_SECONDS` 等 | 见 `.env.example` | Serper 地址、超时、结果数与缓存 |
| `GOOGLE_PLACES_API_KEY`、`ENABLE_GOOGLE_PLACES`、`ENABLE_GOOGLE_OFFICIAL_FALLBACK` | 空 / false | Google Places 隔离 PoC，默认关闭，不进生产管线 |
| 一组 `OFFICIAL_*` | 见 `.env.example` | 官网抓取开关、并发、缓存、超时与页面大小限制 |
| `TASK_SAFETY_TIMEOUT_SECONDS` | 1800 | Worker 防永久卡死的任务安全上限，不是正常生成预算 |
| 一组 `PACK_TIMEOUT_*` | 210 | 地点 / 骨架 / 单日 / 行前 / 语言各 Pack 的独立时限 |
| 一组 `LLM_*_TIMEOUT_*`、`MAX_PROVIDER_RETRIES`、退避配置 | 见 `.env.example` | 模型调用超时与一次额外重试策略 |
| `ENABLE_DEV_MODES` | 0 | 为 1 时开放 MOCK / REPLAY 与完整错误追踪调试接口 |
| `LOG_LEVEL` / `WORKER_LEASE_SECONDS` | INFO / 45 | 日志级别与任务租约有效期 |

## 高德 JS API 安全密钥代理（官方"代理服务器转发"方案）

生产环境下前端不再明文携带 `securityJsCode`，而是把 JSAPI 的高德服务请求统一导向本服务的同源代理前缀 `/_AMapService`（高德官方规定的固定前缀，经 Vercel rewrite 转发）：

```text
前端 JSAPI 请求  /_AMapService/<高德接口路径>?key=<JS Key>&ts=<时间戳>&...
        │（经 Vercel rewrite 同源转发）
        ▼
本服务在查询参数中注入 jscode=AMAP_SECURITY_JS_CODE，按路径分发：
  /v4/map/styles/**   → https://webapi.amap.com
  /v3/vectormap/**    → https://fmap01.amap.com
  其他 /v3/ /v4/ /v5/ → https://restapi.amap.com
        ▼
高德响应（含 v3/assistant/security/jscode 返回的动态密钥）原样透传给前端
```

- 浏览器端只暴露 JS Key（由高德控制台域名白名单保护），安全码只存在于服务端；动态密钥由 JSAPI 持 JS Key 经本代理换取。
- 代理只允许转发白名单路径到高德官方主机，不会成为开放代理。
- 本服务自身的 POI / 路线等 Web 服务调用仍在服务端直接使用 `AMAP_WEB_SERVICE_KEY`，与该代理无关。

## API 摘要

- `POST /api/build`：提交偏好创建生成任务，返回 202 与 `job_id`；支持 `Idempotency-Key` 幂等提交；请求头 `X-Agent-Mode: MOCK` 仅在 `ENABLE_DEV_MODES=1` 时可用。
- `GET /api/build/{id}`：任务状态（queued / running / repairing / validating / paused / done / done_with_warnings / failed）、阶段、Pack 进度、各类指标与用户可读错误。
- `POST /api/build/{id}/resume`：从暂停的 Pack 断点继续，已通过的资料包不会重新生成。
- `GET /api/build/{id}/result`：完整 `DestinationProfile`，仅完成态返回 200。
- `GET /api/trips`：已完成档案摘要列表，按更新时间倒序。
- 编辑与重规划：`.../stops/search-add`、`.../custom-activities`、`PATCH/DELETE .../stops/{place_id}`、日级 / 路段交通覆盖、单日与整趟 `replan-preview`、`replan-previews/{id}/confirm`、`undo`、`replan-options`、`replans`。
- 错误约定：422 返回 `error.details[].pointer`；404 未知任务；核心行程未完成或任务失败时结果接口返回 409；503 数据库不可用；补充模块超时会返回结构完整、带 `enrichment.pending_packs` 标记的核心档案。
- 输入边界：行程 1–30 天；目的地最多 8 个且不多于旅行天数；成人 / 儿童 / 老人各 0–12 且合计至少 1 人；预算非负，分类预算之和不能超过总预算。

快速验证（Windows PowerShell 中使用 `curl.exe`）：

```powershell
# 1. 提交测试偏好，返回 202 和 job_id
curl.exe -sS -X POST http://localhost:8000/api/build -H "Content-Type: application/json" `
  --data-binary "@examples/hangzhou-preferences.json"

# 2. 轮询状态，直到 done / paused / failed
curl.exe -sS "http://localhost:8000/api/build/<job_id>"

# 3. paused 时继续
curl.exe -sS -X POST "http://localhost:8000/api/build/<job_id>/resume"

# 4. 获取完整结果
curl.exe -sS "http://localhost:8000/api/build/<job_id>/result" -o result.json
```

## 生成与降级策略（要点）

- **Pack 管线**：framing → places-core / experiences / food → itinerary-plan → 逐日 itinerary-day → coherence → practical / language 补充包 → destination-profile。每个 Pack 立即落库并带 prompt / schema / model 版本签名；单 Pack 超时进入 paused，resume 只重跑断点及下游；行前与语言这类 enrichment 包超时不阻塞核心档案。
- **调度器**：高德真实路线 + 缓冲 + 用餐时间窗 + 节奏目标（轻松 55%–70% / 均衡 65%–80% / 紧凑 75%–90%）。低密度日最多执行两轮 Fill Pass，只从当天未分配的 preferred / backup 候选中补充；补不进则保留低利用率并写入 warning，不阻塞出档。
- **POI 与路线**：模型只产出候选名称，高德检索后结合城市、区域、类别、上下文行程与绕行成本做消歧，绑定 canonical ID 与坐标；估算 / 未解析路线显式标记 `estimated` / `unresolved`，绝不写"0 分钟"。
- **兴趣系统**：UI 标签映射为稳定内部 taxonomy，后端确定性计算语义标签、affinity、覆盖度与多样性；模型不再输出权威 covered_interests。普通兴趣匹配不足只出 warning；must-go、avoid、非法引用、城市 / 日期冲突与 ownership 冲突才是硬错误。
- **可编辑重规划**：新增地点默认 non-destructive；自然语言只编译为结构化约束，新增名称仍需高德解析，最终时间由 Scheduler 决定；大改先产出 30 分钟有效的 diff 预览，确认才写 day revision；每次只重建当天、相关路线、coherence 与最终 profile，其他日期与研究包不动，支持撤销。
- **事实核验**：官网候选仅来自人工核验注册表与可信 provider 元数据；页面抓取记录 URL、HTTP 状态、内容哈希与时间并缓存；官网与高德冲突时双来源保留、采用官网值并标记 `conflicting`。开放时间 / 票价 / 预约等无权威来源时统一 `needs_recheck`，绝不编造。Serper 只用于发现官网 URL，不作为事实来源。
- **离线降级**：`app/offline.py` 是手写杭州知识档案（10 景点 / 6 体验 / 6 餐厅 / 2 连锁备选 / 清单与语言内容），仅覆盖杭州；其他城市故障进入 paused，不伪造地点；不可恢复错误明确 failed。
- 全链路契约矩阵、Hard/Soft 分类与历史漂移审计见 `schemas/CONTRACT_AUDIT.md` 与 `schemas/DERIVATION.md`；Pack 契约在 `app/contracts.py` 注册版本，`scripts/audit_pack_contracts.py` 可只读审计已有库。

## Benchmark / REPLAY / 迁移

`benchmark/cases/` 含 15 组固定输入，覆盖单日 / 长线 9 天、亲子、老人、晚到、早返、大量必去、明确避开、密集兴趣与稀疏地点池等场景：

```powershell
$env:ENABLE_DEV_MODES="1"
.venv\Scripts\python.exe benchmark_runner.py --mode MOCK
.venv\Scripts\python.exe benchmark_runner.py --mode REAL_LLM
.venv\Scripts\python.exe benchmark_runner.py --mode REPLAY --source-map benchmark\replay-sources.json
```

- `MOCK` 只运行杭州固定档案与状态机，不消耗模型 Token；`REAL_LLM` 正常调用智谱；`REPLAY` 从已持久化 Pack 重放 normalization → allocation → scheduler → validation → compile，全程不调用模型，用于回归与调试。结果写入 `benchmark_runs/`。
- REPLAY 创建新任务，不覆盖来源；支持的起点包括 `itinerary-plan`、`itinerary-day-N`、`itinerary-coherence`、`destination-profile`。
- 数据库启动时自动执行 `app/migrations.py` 顺序迁移并记录 `schema_migrations`；有待执行迁移时先用 SQLite backup API 备份到 `data/backups/`。手动查看：`.venv\Scripts\python.exe -m scripts.migrate status`。不要在后端运行时直接替换数据库文件。
- MVP 面向本机单用户：SQLite 启用 WAL，任务通过原子领取、`run_id`、worker lease 与 heartbeat 防止重复执行；建议单 Uvicorn worker 运行，无账户隔离与远程共享。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe export_schema.py
```

测试使用临时数据库、无真实模型调用，覆盖迁移、备份、原子任务领取、幂等提交与恢复、租约、Pack 可观测性、MOCK / REPLAY、POI 解析、路线、节奏与用餐、添加 / 删除 / 替换、跨天重规划、预览确认与契约一致性。`examples/hangzhou-result.json` 是一次离线生成并通过门禁的可检查样例。

## 部署到 Railway

- Runtime 由 Nixpacks 依据 `requirements.txt` 自动识别为 Python。
- Start Command：`uvicorn app.main:app --host 0.0.0.0 --port $PORT`。
- 挂载 Persistent Volume 到 `/data`，设置 `DATABASE_PATH=/data/travel.db`；首次启动自动建表迁移。
- **示例数据自动导入**：空数据卷首次启动（目标库不存在，或 `jobs` 表为空）时，自动把仓库内 `data/sample_travel.db`（兰州 4 天、上海+苏州 6 天两份脱敏示例）复制为 `/data/travel.db`，让线上"我的行程"开箱即有可浏览的真实档案；一旦库中出现过任意任务，后续部署永不再覆盖，用户数据不受影响。
- 服务端环境变量：`ZHIPU_API_KEY`、`AMAP_WEB_SERVICE_KEY`（或 `AMAP_API_KEY`）、`AMAP_SECURITY_JS_CODE`、`SERPER_API_KEY`（可选）、`DATABASE_PATH`；生产保持 `ENABLE_DEV_MODES=0`。
- 健康检查使用根路径 `GET /`（返回服务状态）。
- 单实例运行：SQLite + 进程内任务队列依赖单一 Worker；横向扩容需要先迁移到 PostgreSQL（Roadmap P3）。
- 前端通过 Vercel rewrite 同源转发访问本服务，无需配置 CORS。

## 来源与许可证

基于 TokenHungryMash/personalized-travel-guide-skill，锁定提交 `7372e4762eea2767bd0a49c949f33c70bed92a67`。上游原始 schema、contract、内容模型与三个校验入口保存在 `third_party/personalized-travel-guide`，仅作来源审阅，不执行其 HTML 构建流程，也未复制其 UI、canonical 或问卷资产。

Copyright (c) 2026 Personalized Travel Guide contributors

完整 MIT 声明见 `third_party/personalized-travel-guide/LICENSE`。
