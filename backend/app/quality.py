"""Soft destination-profile quality metrics. These findings never block compilation."""
from __future__ import annotations

import re

from .interests import normalize_interests


def recommended_food_scene_target(days: int, food_interest: bool) -> int:
    base=1 if days<=2 else (2 if days<=4 else (3 if days<=7 else 4))
    return min(4,base+(1 if food_interest else 0))


def evaluate_profile_quality(t, places: list[dict], itinerary: list[dict], modules: dict,
                             preference_evaluation: dict) -> dict:
    by_id={p['id']:p for p in places}
    food_interest='food' in normalize_interests(t.preferences.interests)
    dedicated_ids=[r['place_id'] for r in modules['food']['dedicated_trip']]
    restaurants=[by_id[pid] for pid in dedicated_ids if pid in by_id and by_id[pid].get('type')=='restaurant']
    scenes={str(p.get('cuisine','')).strip() for p in restaurants if str(p.get('cuisine','')).strip()}
    target=recommended_food_scene_target(t.days,food_interest)

    represented_cities={p.get('city') for p in restaurants}
    city_coverage=len(set(t.destinations)&represented_cities)/max(1,len(t.destinations))
    diversity=min(1.0,len(scenes)/max(1,target))
    generic={'中餐','餐厅','当地菜','本地菜','家常菜','其他','综合菜'}
    locally_relevant=sum(1 for p in restaurants if str(p.get('cuisine','')).strip() not in generic)
    local_relevance=locally_relevant/max(1,len(restaurants))
    food_match=float(preference_evaluation.get('interest_strength',{}).get('food',0))
    if not food_interest:
        food_match=max((float(p.get('interest_affinity',{}).get('food',0)) for p in restaurants),default=0)
    meal_days=sum(any(by_id.get(stop['place_id'],{}).get('type')=='restaurant' for stop in day.get('stops',[])) for day in itinerary)
    meal_coverage=meal_days/max(1,len(itinerary))
    score=100*(.25*city_coverage+.2*diversity+.2*local_relevance+.2*food_match+.15*meal_coverage)

    warnings=[]
    def warn(pointer,code,message):warnings.append({'pointer':pointer,'code':code,'message':message})
    if len(scenes)<target:
        warn('/module_groups/food','FOOD_SCENE_DIVERSITY_LOW',
             f'本次餐饮场景较集中：当前{len(scenes)}类，按行程长度建议约{target}类。')
    if food_interest and city_coverage<1:
        warn('/module_groups/food','FOOD_CITY_COVERAGE_LOW','部分目的城市尚无明确的当地餐饮代表。')
    if food_interest and local_relevance<.5:
        warn('/module_groups/food','LOCAL_FOOD_RELEVANCE_LOW','当地饮食特色表达偏弱。')
    if meal_coverage<1:
        warn('/itinerary','MEAL_SCHEDULE_COVERAGE_LOW','部分日期没有明确的用餐停靠点。')

    experience_types={by_id.get(r['place_id'],{}).get('experience_type') for g in modules['experiences'] for r in g['items']}
    experience_types.discard(None);experience_types.discard('')
    if len(experience_types)<3:
        warn('/module_groups/experiences','EXPERIENCE_DIVERSITY_LOW','体验类型较集中。')
    if any(len(item['note'])<34 for group in modules['travel_notes'] for item in group['items']):
        warn('/module_groups/travel_notes','TRAVEL_NOTE_DETAIL_LOW','部分贴士说明较简略。')
    language=modules['language']
    for family in (language['keyword_groups'],language['phrase_groups']):
        if (len({g['title'] for g in family})<len(family)
                or any(not re.search('[\u4e00-\u9fff]',g['title']) for g in family)
                or any(len({x['text'] for x in g['items']})<len(g['items']) for g in family)):
            warn('/module_groups/language','LANGUAGE_DIVERSITY_LOW','部分语言分组或条目内容重复。');break

    return {
        'food':{
            'recommended_scene_target':target,'actual_scene_count':len(scenes),
            'food_city_coverage':round(city_coverage,2),'food_scene_diversity':round(diversity,2),
            'local_food_relevance':round(local_relevance,2),'food_interest_match':round(food_match,2),
            'meal_schedule_coverage':round(meal_coverage,2),'food_quality_score':round(score,1),
        },
        'warnings':warnings,
    }
