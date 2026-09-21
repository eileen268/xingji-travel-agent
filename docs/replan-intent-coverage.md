# Replan Intent Coverage Matrix

Canonical contract: `StructuredReplanRequest(scope, actions[], affected_day_ids, intent_summary)`.
The parser may emit names and queries; only the backend resolves canonical place IDs, ownership, routes and schedules.

| # | User intent / example | Canonical action | Scope | Backend execution | Preview / protection | Main business error | Regression |
|---:|---|---|---|---|---|---|---|
| 1 | 晚上加东方明珠 | `add_place` + evening | current_day | Canonical/Amap resolution, then conservative insertion around frozen existing stops | Preview before commit; existing stop order/times are preserved | PLACE_NOT_FOUND / AMBIGUOUS / INSERTION_CONFLICT | matrix + search-add endpoint |
| 2 | 下午3点去武康大楼 | `add_place` + hard `15:00` | current_day | Recalculate only adjacent routes; compare earliest arrival with requested time | Existing stops remain protected; conflict returns options instead of removing a stop | OPENING_HOURS_CONFLICT / INSERTION_CONFLICT | matrix + preservation/conflict tests |
| 3 | 删除田子坊 | `remove_place` | current_day | Release ownership, invalidate affected route edges, reschedule day | Preview; original remains until confirm | LOCKED_STOP_CONFLICT | matrix + endpoint delete tests |
| 4 | 其余行程删除 | `remove_place(selector=current_day_remaining)` | current_day | Computes current-day remainder deterministically | Cannot expand to whole trip | LOCKED_STOP_CONFLICT | matrix + dedicated-day tests |
| 5 | 保留博物馆，其余删掉 | `keep_place` + relative `remove_place` | current_day | Keep/must-keep first, then remove remainder | Supporting meal/hotel/transport items remain | LOCKED_STOP_CONFLICT | matrix |
| 6 | 锁住这个景点 | `keep_place(lock=true)` | current_day | Uses selected-stop UI context; fixes schedule position/time | Later removal/move is rejected | LOCKED_STOP_CONFLICT | relative-reference test |
| 7 | 把田子坊换成武康大楼 | `replace_place` | current_day | Resolve new POI, release old, schedule replacement | One diff with remove/add | PLACE_NOT_FOUND / AMBIGUOUS | matrix |
| 8 | 这个餐厅换一家本帮菜 | `replace_place(replacement_preference)` | current_day | Selects only from legal candidate pool | No invented restaurant | NO_FEASIBLE_SLOT | action schema/normalizer tests |
| 9 | 把博物馆放上午 | `move_place` | current_day | Adds preferred time constraint; scheduler chooses exact time | Preview time diff | NO_FEASIBLE_SLOT | matrix |
| 10 | 把博物馆挪到第三天 | `move_place(target_day_id=3)` | affected_days | Release/assign ownership; reschedule source and target only | Cross-day notice, then preview | CROSS_CITY_CONFLICT / DAY_CAPACITY_EXCEEDED | matrix + cross-day preview tests |
| 11 | 把今天下午的两个地方移到第四天 | `move_place(selector=current_day_afternoon)` | affected_days | Resolves affected stop set from current UI day | Other days remain byte-for-byte stable | CAPACITY / LOCKED / CROSS_CITY | relative-reference test |
| 12 | 迪士尼安排一天 | `dedicate_day` | current_day | Promotes verified POI to anchor; removes other major activities | Meals/hotel/transport/supporting activities remain | PLACE_NOT_FOUND / AMBIGUOUS | matrix + dedicated endpoint tests |
| 13 | 迪士尼一天，其余删除 | `dedicate_day(redistribute=false)` | current_day | Current-day major stops removed | Other days unchanged | LOCKED_STOP_CONFLICT | matrix + endpoint test |
| 14 | 迪士尼一天，其余放别天 | `dedicate_day(redistribute=true)` | affected_days | Capacity-scored deterministic redistribution | Cross-day notice; only receiving days change | NO_FEASIBLE_SCHEDULE | matrix + cross-day preview/undo test |
| 15 | 这些地点放到其他天 | `redistribute_stops` | affected_days | Release, capacity score, allocate, reschedule affected days | Unplaceable stops listed; never forced | NO_FEASIBLE_SCHEDULE | parser/scope tests |
| 16 | 今天尽量打车 | `set_transport(level=day, driving)` | current_day | Invalidates route by mode and recalculates all day segments | Preview route/time changes | ROUTE_INFEASIBLE | matrix + transport endpoint tests |
| 17 | 外滩到豫园打车 | `set_transport(level=segment, driving)` | current_day | Resolves both current stops and updates explicit segment | Segment shown between the two stops | PLACE_NOT_FOUND / ROUTE_INFEASIBLE | matrix + segment tests |
| 18 | 今天轻松一点 / 少走路 | `set_pace` | current_day | Scheduler pace and walking-load constraint | Preview density/time changes | NO_FEASIBLE_SCHEDULE | matrix + natural-language endpoint test |
| 19 | 晚上留2小时自由活动 | `add_free_time` | current_day | Creates custom activity, not fake POI | Preview custom block | NO_FEASIBLE_SLOT | matrix + custom activity tests |
| 20 | 午饭吃本帮菜 / 午饭12点 | `set_meal_preference` | current_day | Applies meal role, cuisine/avoid list and preferred time | Scheduler keeps meal windows | NO_FEASIBLE_SLOT | matrix |
| 21 | 上午室内，下午外滩 | `set_activity_preference` + `move_place` | current_day | Semantic tags filter candidates; time constraint applied | Multi-action preview | NO_FEASIBLE_SLOT | matrix |
| 22 | 不要博物馆 / 必须安排外滩 | `avoid` / `must_include` | current_day | Place names use backend resolution; categories filter candidates | No LLM-created IDs | PLACE_NOT_FOUND / AMBIGUOUS | matrix |
| 23 | 第三天改去苏州 | `set_city_day` | affected_days | Trip allocator selects unowned verified POIs in the target city and reschedules that day | Cross-day notice and preview | CROSS_CITY_CONFLICT / LOCKED / NO_FEASIBLE | matrix |
| 24 | 苏州多安排一天 | `set_city_day(day_delta=1)` | affected_days | Requires selecting the donor city/day before mutation | Controlled scope confirmation; no silent city swap | SCOPE_CONFIRMATION_REQUIRED | parser/scope tests |
| 25 | 重新安排整个上海段 | `reflow(city=Shanghai)` | affected_days | Reschedules only days belonging to that city | Cross-day preview | NO_FEASIBLE_SCHEDULE | parser/scope tests |
| 26 | 整趟行程少走路 | `set_pace(max_walking_load=low)` | whole_trip | Reuses places; reschedules every day and routes | Strong whole-trip confirmation | NO_FEASIBLE_SCHEDULE | matrix |
| 27 | 所有天都尽量坐地铁 | `set_transport(day/public_transit)` | whole_trip | Recalculates route and schedule for all days | Strong whole-trip confirmation | ROUTE_INFEASIBLE | schema/scope tests |
| 28 | 保留博物馆，删田子坊，下午加武康大楼，尽量打车 | four actions | current_day | Actions execute as one atomic preview | Failure leaves original itinerary unchanged | Specific failing action code | matrix |

## Contract and failure guarantees

- Prompt schema is generated from `StructuredReplanRequest.model_json_schema()`.
- Historical `keep/remove/transportation_preference/*_constraints` shapes are accepted only by `normalize_replan_request()` and immediately converted to actions.
- JSON decode and Pydantic errors receive one schema-only repair attempt; a second failure returns `REPLAN_INSTRUCTION_PARSE_FAILED`.
- Place IDs are resolved by the backend. New POIs never receive IDs from the LLM.
- Natural-language replans always create a preview. Commit and schedule revision happen only after confirmation.
- `current_day`, `affected_days` and `whole_trip` are bounded by explicit language and actual ownership impact.
- Locked/must-keep/user-created content is protected. Preview failure never changes the persisted itinerary.
- `add_place` is non-destructive by default. Only `replace_place`, `remove_place`, `replan_day`, `dedicate_day` or a confirmed wider scope may remove/reorder existing stops.
- Exact times are hard constraints; wording such as “大概/左右” records a bounded tolerance. Insertion conflicts include requested time, earliest feasible arrival, reason and user-selectable alternatives.
