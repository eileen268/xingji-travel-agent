"""Deterministic interest taxonomy, POI semantics, affinity and trip fit."""
from __future__ import annotations

import math
import re

SEMANTIC_VERSION='interest-taxonomy-v2'

RESTAURANT_AFFINITY_CAPS={
    'food':1.0,'cafe_dessert':1.0,'special_experience':.8,
    'history_culture':.3,'photo_atmosphere':.3,'natural_scenery':.1,'outdoor':.1,
    'classic_landmarks':.3,'city_walk':.3,'design_exhibition':.3,
}

INTEREST_LABEL_TO_ID={
    '经典景点':'classic_landmarks','美食':'food','自然风景':'natural_scenery',
    '人文古迹':'history_culture','历史文化':'history_culture','城市漫游':'city_walk',
    '摄影':'photo_atmosphere','拍照与氛围':'photo_atmosphere','特色体验':'special_experience',
    '海岛与沙滩':'island_beach','夜生活':'nightlife','亲子':'family','轻户外':'outdoor',
    '咖啡与甜品':'cafe_dessert','设计与展览':'design_exhibition','影视与流行文化':'film_popculture',
}
INTEREST_ID_TO_LABEL={value:key for key,value in INTEREST_LABEL_TO_ID.items()}
INTEREST_ID_TO_LABEL.update({'history_culture':'历史文化','photo_atmosphere':'拍照与氛围'})

# Stable rules. Interest IDs never participate in free-text matching.
TAG_RULES=(
    (r'丹霞|地质公园|雅丹',('geopark','natural_scenic','desert_landform','landscape_photography','sunset_spot')),
    (r'沙漠|沙丘|戈壁',('desert_landform','natural_scenic','landscape_photography')),
    (r'古城|古镇|老城|古街',('ancient_town','old_town','historic_site','historic_architecture','pedestrian_street')),
    (r'博物馆|纪念馆|陈列馆',('museum','heritage','design_exhibition')),
    (r'遗址|陵|古迹|故宫|城墙',('historic_site','heritage','archaeological_site','historic_architecture')),
    (r'寺|庙|塔|道观|石窟',('temple','heritage','historic_architecture')),
    (r'湖|江|河|溪|泉|瀑布|湿地',('lake','waterfront','natural_scenic','landscape_photography')),
    (r'山|峡谷|森林|草原|国家公园',('mountain','national_park','natural_scenic','outdoor_activity','landscape_photography')),
    (r'公园|植物园|花园',('urban_park','green_space')),
    (r'街区|步行街|里弄|胡同',('urban_neighborhood','pedestrian_street','cityscape')),
    (r'美术馆|艺术|设计|展览',('art_district','design_exhibition')),
    (r'海岛|沙滩|海滨|海岸',('island','beach','natural_scenic','landscape_photography')),
    (r'夜景|夜游|灯光',('night_view','cityscape','photo_spot')),
    (r'亲子|儿童|动物园|海洋馆|科技馆',('family_friendly','interactive')),
    (r'咖啡|甜品|烘焙|茶馆',('cafe','dessert','food_experience')),
    (r'影视|电影|动漫|取景',('film_location','pop_culture','photo_spot')),
)

TAG_AFFINITY={
 'classic_landmarks':{'landmark':.72,'heritage':.78,'historic_site':.82,'national_park':.72,'museum':.65},
 'food':{'restaurant':.9,'food_experience':.9,'food_workshop':.95,'cafe':.5,'dessert':.65},
 'natural_scenery':{'natural_scenic':.82,'mountain':.9,'lake':.84,'desert_landform':.92,'geopark':.95,'national_park':.92,'green_space':.38,'urban_park':.42,'waterfront':.62},
 'history_culture':{'historic_site':.9,'heritage':.88,'museum':.9,'temple':.82,'ancient_town':.84,'historic_architecture':.8,'archaeological_site':.95},
 'city_walk':{'pedestrian_street':.82,'historic_district':.86,'old_town':.84,'urban_neighborhood':.8,'waterfront':.65,'cityscape':.62},
 'photo_atmosphere':{'landscape_photography':.9,'sunset_spot':.9,'night_view':.82,'cityscape':.68,'photo_spot':.82,'historic_architecture':.72,'old_town':.76,'art_district':.76,'natural_scenic':.72,'desert_landform':.9,'geopark':.9},
 'special_experience':{'experience':.78,'interactive':.72,'food_workshop':.9,'art_district':.58},
 'island_beach':{'island':.95,'beach':.95},'nightlife':{'night_view':.55,'nightlife':.95},
 'family':{'family_friendly':.9,'interactive':.72,'urban_park':.4},
 'outdoor':{'outdoor_activity':.9,'national_park':.82,'mountain':.8,'natural_scenic':.6},
 'cafe_dessert':{'cafe':.92,'dessert':.92},
 'design_exhibition':{'design_exhibition':.9,'art_district':.86,'museum':.55},
 'film_popculture':{'film_location':.92,'pop_culture':.92},
}
KNOWN_TAGS={tag for mapping in TAG_AFFINITY.values() for tag in mapping}|{
    tag for _,tags in TAG_RULES for tag in tags}|{'landmark','experience','restaurant','special_experience'}


def normalize_interests(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(INTEREST_LABEL_TO_ID.get(label,label if label in TAG_AFFINITY else f'custom:{label}') for label in labels))


def _semantic_tags(place: dict) -> tuple[list[str],list[str]]:
    tags=[tag for tag in (place.get('semantic_tags') or []) if tag in KNOWN_TAGS]
    sources=list(place.get('semantic_source') or [])
    kind=place.get('type')
    if kind=='sight':tags.append('landmark')
    elif kind=='experience':tags.append('experience')
    elif kind in ('restaurant','chain'):tags.append('restaurant')
    experience=place.get('experience_type','')
    if experience=='food_workshop':tags.extend(('food_workshop','food_experience'))
    elif experience=='nature':tags.extend(('natural_scenic','outdoor_activity'))
    elif experience=='culture':tags.extend(('heritage','special_experience'))
    text=' '.join(str(place.get(key,'')) for key in ('display_name','local_name','description','cuisine','signature_dishes','experience_type'))
    for pattern,matched in TAG_RULES:
        if re.search(pattern,text,re.I):tags.extend(matched)
    if not sources:sources.append('backend_rule')
    return list(dict.fromkeys(tags)),list(dict.fromkeys(sources))


def enrich_place(place: dict) -> dict:
    if place.get('semantic_version')==SEMANTIC_VERSION and place.get('semantic_tags') and place.get('interest_affinity'):
        return place
    tags,sources=_semantic_tags(place); affinity={}; confidence={}; reasons={}
    for interest,mapping in TAG_AFFINITY.items():
        matched=[tag for tag in tags if tag in mapping]
        if not matched:continue
        values=sorted((mapping[tag] for tag in matched),reverse=True)
        score=min(1.0,values[0]+(.06 if len(values)>1 else 0)+(.03 if len(values)>2 else 0))
        affinity[interest]=round(score,2);reasons[interest]=matched
        confidence[interest]='high' if values[0]>=.8 and any(tag not in ('landmark','experience','restaurant') for tag in matched) else 'medium'
    if place.get('type') in ('restaurant','chain'):
        for interest in list(affinity):
            cap=RESTAURANT_AFFINITY_CAPS.get(interest,.3)
            affinity[interest]=round(min(affinity[interest],cap),2)
            if affinity[interest]<=0:affinity.pop(interest,None);confidence.pop(interest,None);reasons.pop(interest,None)
    place['semantic_tags']=tags;place['semantic_source']=sources;place['interest_affinity']=affinity
    place['semantic_confidence']=confidence;place['affinity_reasons']=reasons
    place['semantic_version']=SEMANTIC_VERSION
    return place


def enrich_places(places: list[dict]) -> list[dict]:
    for place in places:enrich_place(place)
    return places


def place_interest_score(place: dict, interest_ids: list[str]) -> float:
    values=sorted((float(place.get('interest_affinity',{}).get(i,0)) for i in interest_ids),reverse=True)
    return min(1.0,(values[0] if values else 0)+(.25*values[1] if len(values)>1 else 0))


def derive_day_interests(day: dict, places: list[dict], selected_interests: list[str]) -> dict[str,float]:
    by_id={p['id']:p for p in places}; interest_ids=normalize_interests(selected_interests); result={}
    for interest in interest_ids:
        values=sorted((float(by_id.get(stop['place_id'],{}).get('interest_affinity',{}).get(interest,0)) for stop in day.get('stops',[])),reverse=True)
        if values and values[0]>=.3:result[interest]=round(min(1.0,values[0]+(.25*values[1] if len(values)>1 else 0)),2)
    return result


def evaluate_trip_preferences(itinerary: list[dict], places: list[dict], selected_interests: list[str]) -> dict:
    ids=normalize_interests(selected_interests); per_interest={interest:[] for interest in ids}
    for day in itinerary:
        strengths=derive_day_interests(day,places,selected_interests)
        day['derived_interests']=list(strengths);day['day_interest_strength']=strengths
        for interest,value in strengths.items():per_interest[interest].append(value)
    strength={interest:round(min(1.0,max(values,default=0)+(.25*sorted(values,reverse=True)[1] if len(values)>1 else 0)),2)
              for interest,values in per_interest.items()}
    covered=[interest for interest,value in strength.items() if value>=.4]
    coverage=1.0 if not ids else len(covered)/len(ids)
    positive=[value for value in strength.values() if value>0]
    if len(positive)<=1:diversity=1.0 if len(ids)<=1 and positive else (0.0 if len(ids)>1 else 1.0)
    else:
        total=sum(positive); entropy=-sum((v/total)*math.log(v/total) for v in positive)
        diversity=entropy/math.log(len(ids)) if len(ids)>1 else 1.0
    mean_strength=sum(strength.values())/len(ids) if ids else 1.0
    score=round(100*(.45*coverage+.4*mean_strength+.15*diversity),1)
    warnings=[]
    for interest,value in strength.items():
        if value<.4:warnings.append({'pointer':'/preference_evaluation','code':'INTEREST_WEAK_MATCH',
            'message':f'{INTEREST_ID_TO_LABEL.get(interest,interest)}偏好覆盖较弱','interest_id':interest,'strength':value})
    if len(ids)>len(itinerary)*2:warnings.append({'pointer':'/preference_evaluation','code':'INTEREST_DENSITY_HIGH',
        'message':'旅行天数较短且兴趣较多，已优先保证路线质量与节奏。'})
    return {'selected_interest_ids':ids,'interest_coverage':round(coverage,2),'interest_strength':strength,
            'interest_diversity':round(diversity,2),'preference_fit_score':score,'warnings':warnings}
