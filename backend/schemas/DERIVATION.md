# 七模块契约派生记录

来源： https://github.com/TokenHungryMash/personalized-travel-guide-skill/tree/7372e4762eea2767bd0a49c949f33c70bed92a67

Copyright (c) 2026 Personalized Travel Guide contributors，MIT；完整声明见 ../third_party/personalized-travel-guide/LICENSE。

## 原 schema → seven-v1

- 保留 itinerary、places、module_groups 关联思路，增加版本、任务ID、生成模式和待复核警告。
- 删除 module_groups.shopping、shop/souvenir 类型、shopping_advice。用户原始 budget.shopping 与兴趣仍按创建表单接受；这不是生成购物内容。
- 移除 cover/UI 绑定、render_bindings_file、transport/stays 必需项和图片审计字段：本版仅生成 JSON，不推荐航班或酒店，不生成 HTML。
- 新增 sights.scheduled / optional，通过 place_id 引用地点；每日停靠点与景点分组必须一致。
- place.official_url 必存在且可 null；map_url 必存在且为 HTTPS 搜索入口。MVP 未核验坐标，因此 coordinates 固定 null。rating/review_count 固定 null。
- place 增加持久化的 semantic_tags、interest_affinity、semantic_source、semantic_confidence 与 affinity_reasons；Day 增加后端推导的 derived_interests/day_interest_strength；档案增加 trip-level preference_evaluation。
- 费用、路程和交通时长固定 null，配套保守文字；计划时刻和建议停留时长仍为可排程的数值。
- trip 完整使用当前前端表单命名和约束。节奏使用 intensive，取代原 full。
- preparation 使用 essentials/confirm_ahead；完整 enrichment 的中长行程目标仍为24项，1–2天缩为12项。核心行程降级结果可暂为空，应用校验始终检查标题与ID唯一。
- food.menu_guide 合并原 menu_guide 与 menu_primer：title/intro/cards/dictionary 是唯一菜单入门。小吃4、连锁2–4；餐厅研究按城市每批2–6家，单城市以6家为生成目标，多城市总量不受小型全局 maxItems 限制。
- language 的中长行程目标为关键词与短语各5×5，1–2天缩为各3组×3条；贴士相应由5类×4主题缩为3类×2主题。条目统一 text/meaning/pronunciation。
- 每个对象 additionalProperties=false；所有生成结果必须通过 Pydantic v2 与 JSON Schema，检查空值与字段类型。
- Pydantic 源在 app/models.py；export_schema.py 导出两份静态交付契约。运行时另保存 framing、三类地点、itinerary-plan、itinerary-allocation、逐日行程、全程一致性、两个模块包及 destination-profile；最终仍编译成4个公开研究包，不依赖原 renderer。
- 入参 preferences 增加与 `references/first-use-intake.md` 问卷一致的 budgetLevel、mustGo、avoid、constraints；均有中性默认值，旧档案仍可读取。交通与住宿字段保留为兼容字段，但新创建页不把它们作为推荐条件询问。

## 校验实现覆盖

完整实现：包类型/必填路径/动态数组预算；全档案 schema；日期连续覆盖；目的城市集合与顺序；唯一地点ID和同类地点名称；按城市停留天数计算景点、体验、餐厅预算；引用存在性与类别；景点安排/备选分区；餐厅每城2–6并优先保持餐饮场景多样性；清单唯一性；语言和贴士分类唯一性；动态数字、评分、购物模块禁入；研究包与结果逐字段一致；核心档案通过后才提交可查看结果。

简化与原因：

- 不执行原 HTML、浏览器、图片及媒体真实性门禁，因为没有生成那些制品。保留原入口脚本供对照，不宣称上游整体 HANDOFF ALLOWED。
- 不验证官网是否可访问、门店营业或准确地理位置，不写“已核验”状态；MVP 明确不接搜索。动态信息采用保守文字，字段为空。
- 交通不校验真实路网，计划时刻只查顺序与跨日，不声称路线经过道路服务验证。
- 抵达/返程日允许少于完整日停靠数量，避免为凑数塞满边界时段。
- 检查内容长度、标题、去重和引用，但不把字符长度等同于事实正确性；兴趣匹配与体力优化依赖生成内容，离线仅作保守参考。
- 有界 LLM 子阶段最多一次定向修复。处理顺序为安全 JSON 清理、结构校验、服务端规范化、确定性修复、语义校验、一次定向 LLM 修复、再次规范化与最终校验。
- 每天先通过 `validate_day`，再由 `validate_trip_coherence` 检查跨日重复、骨架锚点与避开项。全程错误只使对应日期及其依赖包失效；其他日期与语言包继续保留。
- 每个运行时包在 SQLite 中保存状态、内容、校验错误、输入签名，以及生成、修复、恢复三类计数。签名包含规范化入参、直接依赖签名和 prompt/schema/pipeline 版本。
- `itinerary-plan` 的模型输出仅包含区域标签、合法地点 ID 与自然语言主题；day/date/city 由后端注入。主锚点和候选地点使用按天生成的允许集合，模型不能创建新 ID，也不输出权威兴趣声明。
- 骨架增加 `backup_candidate_ids`，并由后端注入 `fixed_meal_stop_id`。Day LLM 使用独立内部契约，只输出 `optional_stop_order`、`day_intent`、`practical_notes`；动态 enum 仅包含当天优先与备用候选，不包含主锚点和餐厅。
- 语义校验前运行 `normalize_day_candidate_pools`：按 primary、preferred、backup 的角色优先级删除低优先级重复引用，保留原顺序且不创建、替换地点。该步骤属于确定性规范化，不占用模型 repair 配额。
- 最终 Day 由确定性 scheduler hydrate：日期、城市、主锚点、固定用餐、顺序、建议时刻和地图空字段均由后端负责。路线 API 未接入时不构成校验失败。
- `itinerary-allocation` 是持久化的 trip-level ownership source of truth。仅 sight / experience 进入 canonical pool；primary 和 preferred 为 hard ownership，backup 为 soft reservation，最终选中的 backup 升级为 hard。available set 可由 canonical IDs 减去 hard ownership 重建。
- 分配器先处理全程 primary，再以区域、主题、interest affinity 与偏好边际增益全局分配 preferred，最后分配 backup，避免 Day 顺序造成先到先得。resume 只 release/reallocate 失败日期，其他有效 Day ownership 保持不变；后端重启后从 SQLite pack 重建。
- UI 兴趣先映射为稳定内部 taxonomy ID；地点只做一次确定性 semantic enrichment 并持久化。Tag → Interest 使用固定权重，affinity 规范化到 0–1 且保存证据标签与置信度。
- restaurant / chain 使用类型专属 affinity cap：food 与 cafe/dessert 保留完整范围，special experience 最高0.8；history/photo 默认最高0.3，natural/outdoor 默认最高0.1。普通餐厅描述中的环保或主题文案不会获得与景点相当的自然、历史或摄影分数。
- `destination-profile` 的硬校验只回答结构是否完整、引用是否合法。原最终门禁中的餐饮场景至少4类、长行程至少3家推荐餐厅、体验达到3种类型、贴士额外34字阈值、语言条目额外去重已移出 hard validator。
- `quality_evaluation.food` 保存动态推荐场景目标、实际场景数、城市覆盖、场景多样性、当地饮食相关性、美食兴趣匹配、用餐日覆盖和0–100质量分。体验、贴士、语言丰富度与餐饮不足均生成 warning，不会使任务 paused，也不会要求 LLM 伪造补齐。
- 兴趣只按实际 stops 推导。`UNSUPPORTED_INTEREST` 不再是硬错误；历史模型声明会被确定性移除。Trip-level 输出 coverage、strength、diversity、preference_fit_score 与 warnings。普通兴趣不足只产生 warning；must-go、avoid、引用、城市、日期和 ownership 继续作为硬门禁。
- 外部服务或可修复内容故障进入 paused；继续接口按依赖图恢复。只有数据库、入参或离线档案自身损坏等不可恢复错误进入 failed。destination-profile 是只编译的最终包，全部门禁通过才原子写入 done。
- 正常执行由 pack-level timeout 控制：places、itinerary-plan、每个 itinerary-day、practical、language-notes 分别计时。task safety ceiling 仅用于回收异常卡死 worker，触发时仍保留所有检查点。
- 每次 LLM 子请求在发出前写入 SQLite，记录 pack、substage、模型、prompt 字符数、估算 token、generation/repair 序号和 running 状态；完成后补 request ID、耗时及细分错误类型。
- practical 与 language-notes 是 enrichment。任一补充包超时会保留结构合法的 deferred 内容、写入 `enrichment.pending_packs` 并完成核心档案为 `done_with_warnings`；resume 可单独重建 timed-out pack。核心 pack 超时仍进入 paused。
- Provider transient retry 与 semantic repair 完全分离。connect/read timeout、connection reset 和 HTTP 5xx 最多额外重试一次，使用1–3秒抖动退避并记录 `provider_retry_count`；它不增加 `repair_attempt_count`。结构或语义失败仍只有一次定向修复。
- Research 按 core、experiences、food 的 pack 边界顺序执行。每个成功 subrequest 的 canonical payload、usage、request ID、max tokens、目标数量和 checkpoint key 立即持久化；pack 一旦收齐必要子请求便立即编译为 valid。Resume 或进程重启只重跑未完成 subrequest。
- Research 的 LLM 输出使用 compact place contract，只包含 ID、名称、类型、城市、简述及该类别必要字段；affinity reasons、semantic source、confidence、provenance 和版本信息由后端补充并持久化，不进入模型输出 schema。餐厅简述限制为600字符，减少 completion 膨胀。
- `Preparation` 是 modules-practical 与 destination-profile 共用的唯一 Pydantic 契约。Schema 导出和运行时验证均直接使用该模型；已移除导出器曾额外注入的“合计至少24项” anyOf。单组仅保留防止异常膨胀的60项上限，内容数量不足进入质量评价而非最终结构门禁。
- JSON Schema 的 oneOf/anyOf 等组合错误会递归展开到叶子错误并持久化，记录 instance/schema path、validator、validator value、message 和 invalid value；不再只保存“not valid under any schemas”顶层提示。

`check_handoff` 的适配等价含义是“研究包、最终数据和持久化交付条件全部通过”，不包括原仓库的视觉交付条件。

完整 pack contract matrix 与兼容策略见 `CONTRACT_AUDIT.md`。运行时不读取 checked-in schema 作为验证真相源；所有 pack 先经过 `app/contracts.py` 中注册的 Pydantic 模型，导出 JSON Schema 与 canonical fixtures 由测试保证同步。
