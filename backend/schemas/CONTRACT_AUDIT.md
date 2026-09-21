# Travel Agent Contract Audit

审计日期：2026-09-14  
审计范围：从 API framing 到 destination-profile，以及最终前端读取映射。运行时结构以 Pydantic 为唯一来源；导出的 JSON Schema 是交付制品。

## Contract Matrix

| Pack | Producer | Canonical Pydantic Model | JSON Schema | Normalizer / Adapter | Semantic Validator | Consumer | Compiler Target |
|---|---|---|---|---|---|---|---|
| framing | FastAPI `BuildInput` | `FramingPack` | Pydantic generated | BuildInput normalization | dates, travelers, destination order, budget allocation | research budgets, planner | `profile.trip` |
| places-core | sight research batches | `PlacesCorePack` | Pydantic generated | compact research + fact hydration + semantic enrichment | sight type, city/count, uniqueness, conservative facts | plan, allocator, scheduler | `profile.places[type=sight]` |
| places-experiences | experience research batches | `PlacesExperiencesPack` | Pydantic generated | compact research + fact hydration + semantic enrichment | experience type/city/count, uniqueness | allocator, practical compiler | `profile.places[type=experience]` and experience groups |
| places-food | restaurant/chain research batches | `PlacesFoodPack` | Pydantic generated | type-aware affinity + deterministic trim | per-city restaurant 2–6, type/city, uniqueness | meal injection, food module | `profile.places[type=restaurant/chain]` and food refs |
| itinerary-plan | LLM decision + backend hydrate | `ItineraryPlan` | dynamic request schema from `ItineraryPlanDecision`; persisted schema from Pydantic | remove legacy declarations, pool normalization, backend day/date/city/meal hydrate | allowed IDs, pool disjointness, must-go/avoid | allocator | internal skeleton |
| itinerary-allocation | deterministic allocator | `ItineraryAllocationPack` | Pydantic generated | explicit legacy field removal | canonical ownership, hard/soft reservation, cross-day uniqueness | day catalog, resume/release | internal ownership source |
| itinerary-day-{n} | DayDecision LLM + scheduler | `Day` | request schema from `DayDecision`; persisted schema from Pydantic | optional ID dedupe, deterministic scheduling and hydration | city/date, references, required anchor/meal, order, duplicates | coherence, compiler | `profile.itinerary[n]` |
| itinerary-coherence | deterministic validators | `CoherencePack` | Pydantic generated | none | references, ownership, cross-day duplicate, must-go/avoid | destination compiler | handoff gate |
| modules-practical | bounded enrichment requests + compiler | `PracticalPack` | request schemas from shared budget factory; persisted schema from Pydantic | canonical preparation/food/experience assembly | structure, refs, preparation ID/title uniqueness | destination compiler | `module_groups.experiences/food/preparation` |
| modules-language-notes | bounded enrichment requests + compiler | `LanguagePack` | request schemas from shared budget factory; persisted schema from Pydantic | category/title injection | category enum and uniqueness | destination compiler | `module_groups.language/travel_notes` |
| destination-profile | deterministic compiler | `DestinationProfile` | Pydantic generated | aggregate three place packs; module groups are derived views | structural integrity, provenance, refs, city/date and hard invariants | API and `frontend/lib/api.ts` | final seven-module JSON |

## Mismatches found and repaired

| # | Pack / boundary | Type | Finding | Resolution | Historical impact |
|---|---|---|---|---|---|
| 1 | modules-practical → destination-profile | structure / legacy rule | schema exporter injected an extra preparation `anyOf` requiring 24 items | removed handwritten schema mutation; both use `Preparation` | 5+5 and 6+6 now valid; old failed final pack can recompile |
| 2 | all runtime validators | source drift | runtime read checked-in JSON Schema, allowing it to drift from Pydantic | runtime now calls `model_json_schema()` directly | no payload migration |
| 3 | nested JSON Schema errors | observability | oneOf/anyOf contexts collapsed to a generic parent message | recursive leaf error expansion and persistence | affects diagnostics only |
| 4 | persisted pack registry | missing contracts | only four aggregate models were registered | every persisted pack and day pattern now has a canonical model | invalid old payload is locally reset; compatible payload is adapted |
| 5 | places-core → compiler | semantic/structure | `places-core` meant sights in SQLite but all POIs during compile | three explicit place packs plus `collect_places()` compiler adapter | old aggregate place payload supported by named legacy adapter |
| 6 | request counts | quantity drift risk | practical/language/research request schemas were created inline | shared request-model factories use the same budget source as prompts | no payload migration |
| 7 | manifest → pack signatures | version drift | manifest and pack graph carried different prompt/pipeline versions | one `versions.py` source and per-pack contract versions | stale v4/v7 metadata migrates only after payload validation |
| 8 | itinerary-plan/allocation | deprecated field | empty `covered_interests` remained persisted after interest derivation moved to actual stops | removed from canonical models and producers | explicit adapter removes historical declarations |
| 9 | final content models | hard/soft classification | minimum prose lengths could pause otherwise usable content | retained non-empty/max-size structure; richness stays in quality warnings | compatible, less restrictive |
| 10 | place references | ID contract | place IDs were generic 3000-character text | canonical ASCII internal ID pattern and 128-character limit | current generated/offline IDs compatible |
| 11 | checked-in pack schema | coverage | schema artifact represented only the old four-pack handoff | exports all pack models, day pattern and version map | artifact format extended |
| 12 | regression assets | testing gap | no canonical producer→consumer fixtures or complete contract compile test | eleven fixtures, schema parity tests and no-LLM E2E compile | no runtime impact |
| 13 | historical allocation/profile | version compatibility | five valid legacy payloads lacked later interest/quality fields | deterministic adapters rebuild defaults and evaluations from persisted trip data | all 157 currently valid persisted payloads now pass canonical contracts |

## Canonical and derived views

- The canonical place sources are `places-core`, `places-experiences`, and `places-food`. `destination-profile.places` is their ordered compiler view.
- The canonical itinerary sources are `itinerary-plan`, `itinerary-allocation`, and each `itinerary-day-{n}`. `destination-profile.itinerary` is the ordered day-pack view.
- `modules-practical.preparation` is canonical module content. `destination-profile.module_groups.preparation` is its derived final view. There is no top-level `destination-profile.preparation`.
- The transient `{ "itinerary": [...] }` wrapper exists only at the handoff/compiler boundary and is not a persisted pack.

## Hard, soft, and warning classification

Hard structural checks: Pydantic/JSON structure, required fields, enums, canonical IDs, valid references, authoritative city/date, ownership, cross-day uniqueness, must-go, explicit avoid, non-overlapping time order, and the research budgets required to form viable candidate pools.

Soft quality checks: interest strength/coverage, food city coverage, scene diversity, local relevance, experience diversity, language/tip richness, and prose detail. These affect scores or `done_with_warnings`; they do not pause the job.

Warnings: unverified hours/fees/reservations, missing map route facts, weak preferences, compact enrichment and deferred enrichment.

## Count-rule review

- Sight and experience research counts come from `budgets.py` and are used by both prompt instructions and request schemas.
- Food uses `FOOD_MIN_PER_CITY=2` and `FOOD_MAX_PER_CITY=6` from one module; overproduction is trimmed before semantic repair.
- Practical and language counts come from `enrichment_budget()` and shared request-model factories.
- Preparation canonical storage has only a 60-item safety ceiling per group. The dynamic request target is not a final hard minimum.
- Final food/experience/language/tip diversity targets remain quality metrics.

## Nullability and factual fields

Until a factual/map API is connected, `coordinates`, `rating`, `review_count`, `transfer_minutes`, `distance_km`, and `estimated_cost` are explicitly null throughout producer, compiler, schema and frontend mapping. `official_url` is nullable. `map_url` is a required deterministic search link.

## Interest contract

UI labels normalize to stable interest IDs. POI `semantic_tags` map deterministically to `interest_affinity`; actual day stops produce `derived_interests` and trip preference evaluation. `covered_interests` and intent-string matching are not runtime sources of truth.

## Version compatibility

New contracts have per-pack versions in `contracts.py`. Two explicit compatibility adapters exist:

1. historical aggregate `places-core` is split by canonical type;
2. historical plan/allocation `covered_interests` is removed.
3. historical allocation interest state receives deterministic defaults;
4. historical destination preference/quality evaluation is recomputed from its persisted trip, places, itinerary and modules.

Stale v4/v7 metadata is upgraded only when the payload passes the new canonical model. A structurally incompatible payload is reset at that pack boundary and normal dependency invalidation applies.

## Change process

Change the canonical Pydantic model first, then update the shared request-model factory or semantic validator, regenerate schemas and fixtures, and run contract plus E2E tests. Removing/renaming fields, tightening types, changing nullability or semantics requires a new pack contract version and an adapter or explicit local regeneration path.
