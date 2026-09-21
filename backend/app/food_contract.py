"""Single source of truth for per-city restaurant research batches."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, create_model

from .interests import enrich_place
from .models import ResearchPlace, StrictModel


FOOD_MIN_PER_CITY = 2
FOOD_MAX_PER_CITY = 6


def food_target_per_city(stay_days: int) -> int:
    """Scale a per-city batch by days spent there."""
    from .budgets import restaurants_for_city
    return max(FOOD_MIN_PER_CITY,min(FOOD_MAX_PER_CITY,restaurants_for_city(stay_days)))


def food_batch_model(city: str):
    batch_place = create_model(
        'RestaurantBatchPlace', __base__=ResearchPlace,
        city=(Literal[city], ...), type=(Literal['restaurant'], ...),
    )
    return create_model(
        'RestaurantBatch', __base__=StrictModel,
        places=(list[batch_place], Field(min_length=FOOD_MIN_PER_CITY, max_length=FOOD_MAX_PER_CITY)),
    )


def food_batch_instruction(city: str, stay_days: int) -> str:
    target = food_target_per_city(stay_days)
    return (
        f'只生成{city}的餐厅。必须返回{FOOD_MIN_PER_CITY}到{FOOD_MAX_PER_CITY}个，建议{target}个；'
        '优先本地代表性、餐饮场景差异和菜系多样性。不要为了兴趣标签虚构地点特征。'
    )


def normalize_food_batch(data: dict, city: str) -> dict:
    """Safely trim overproduction before JSON Schema validation; never invent entries."""
    if not isinstance(data, dict) or not isinstance(data.get('places'), list):
        return data
    items = data['places']
    if len(items) <= FOOD_MAX_PER_CITY:
        return data

    ranked = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            ranked.append((index, item, '', -100.0))
            continue
        # Affinity is used only to rank excess candidates.  Keep the original
        # item untouched so enrichment-only fields cannot leak into the compact
        # research response contract.
        scored_item = dict(item)
        enrich_place(scored_item)
        cuisine = str(scored_item.get('cuisine', '')).strip().lower()
        city_match = scored_item.get('city') == city
        type_match = scored_item.get('type') == 'restaurant'
        local_text = ' '.join(str(scored_item.get(key, '')) for key in ('display_name', 'description', 'cuisine'))
        local_relevance = 1.0 if city and city in local_text else 0.0
        food_affinity = float(scored_item.get('interest_affinity', {}).get('food', 0))
        base = 4.0 * city_match + 3.0 * type_match + food_affinity + .25 * local_relevance
        ranked.append((index, item, cuisine, base))

    selected = []
    used_cuisines = set()
    remaining = list(ranked)
    while remaining and len(selected) < FOOD_MAX_PER_CITY:
        best = max(remaining, key=lambda row: (row[3] + (0.6 if row[2] and row[2] not in used_cuisines else 0), -row[0]))
        remaining.remove(best)
        selected.append(best)
        if best[2]:
            used_cuisines.add(best[2])
    selected.sort(key=lambda row: row[0])
    return {**data, 'places': [row[1] for row in selected]}
