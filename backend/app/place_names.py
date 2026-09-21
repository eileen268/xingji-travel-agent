"""Canonical POI naming and locale-aware display rules."""
from __future__ import annotations

import copy
import re
from urllib.parse import quote


CJK_RE=re.compile(r'[\u3400-\u9fff]')
LATIN_RE=re.compile(r'[A-Za-z]')


def has_chinese(value: str | None) -> bool:
    return bool(value and CJK_RE.search(value))


def primarily_english(value: str | None) -> bool:
    if not value:return False
    chinese=len(CJK_RE.findall(value));latin=len(LATIN_RE.findall(value))
    return latin>0 and latin>chinese*2


def _map_url(city: str, name: str) -> str:
    return f'https://uri.amap.com/search?keyword={quote(city+" "+name)}&city={quote(city)}'


def normalize_place_names(place: dict, *, locale: str='zh-CN', provider_output: bool=False) -> dict:
    """Normalize names without translating or inventing a new place identity."""
    item=dict(place)
    original_display=str(item.get('display_name') or '').strip()
    local=str(item.get('local_name') or '').strip()
    canonical=str(item.get('canonical_name') or '').strip()
    english=item.get('english_name')
    english=str(english).strip() if english else None

    # A provider-local name may legitimately mix a Chinese brand/location with
    # Latin branding. It is not an English alias unless it contains no Chinese.
    if not english and primarily_english(original_display) and not has_chinese(original_display):english=original_display
    if english and has_chinese(english) and english==local:english=None
    if not local:
        local=canonical or original_display
    if not canonical:
        canonical=local if has_chinese(local) else original_display or local
    display=(local if locale.lower().startswith('zh') and has_chinese(local) else canonical or local or original_display)

    item.update(canonical_name=canonical,local_name=local,english_name=english,display_name=display)
    place_type=item.get('type')
    if place_type=='experience':
        item['experience_title']=str(item.get('experience_title') or display).strip()
        # Provider-generated experiences cannot establish a real booking product or
        # a linked POI. Those facts require a future official/map source.
        if provider_output:
            item['linked_place_id']=None
            item['booking_status']='unverified'
            item['entity_kind']='experience_concept'
            item['verification_status']='unverified'
            item['name_source']='model_knowledge'
        else:
            item.setdefault('linked_place_id',None)
            item.setdefault('booking_status','unverified')
            item.setdefault('entity_kind','experience_concept' if not item.get('linked_place_id') else 'poi')
            item.setdefault('verification_status','unverified')
            item.setdefault('name_source','model_knowledge')
    else:
        item.update(experience_title=None,linked_place_id=None,booking_status='not_applicable',entity_kind='poi')
        item.setdefault('verification_status','unverified')
        item.setdefault('name_source','model_knowledge')

    default_provider='offline_reference' if item.get('knowledge_status')=='offline_reference' else 'llm_generated'
    item.setdefault('provider',default_provider)
    item.setdefault('provider_place_id',None)
    item.setdefault('district',None);item.setdefault('address',None)
    item.setdefault('latitude',None);item.setdefault('longitude',None)
    item.setdefault('category','');item.setdefault('subcategory',None)
    item.setdefault('place_type','generated_experience' if item.get('entity_kind')=='experience_concept' and not item.get('linked_place_id') else item.get('type'))
    item.setdefault('source_confidence',None);item.setdefault('source_metadata',{})
    item.setdefault('fact_provenance',{})
    if (not provider_output and item.get('entity_kind')=='experience_concept' and not item.get('linked_place_id')
            and item.get('provider')=='llm_generated'):
        item['verification_status']='generated'

    if item.get('entity_kind')=='experience_concept' and not item.get('linked_place_id') and 'map_url' in item:
        item['map_url']=None
    elif item.get('city') and display and 'map_url' in item:
        item['map_url']=_map_url(str(item['city']),display)
    return item


def normalize_places(places: list[dict], *, locale: str='zh-CN', provider_output: bool=False) -> list[dict]:
    return [normalize_place_names(place,locale=locale,provider_output=provider_output) for place in places]


def normalize_profile_names(profile: dict, *, locale: str='zh-CN') -> dict:
    """Normalize a compiled/current profile and replace stale display text deterministically."""
    result=copy.deepcopy(profile)
    old_places=result.get('places') or []
    replacements={}
    normalized=[]
    for place in old_places:
        old=str(place.get('display_name') or '')
        current=normalize_place_names(place,locale=locale)
        if old and old!=current['display_name']:replacements[old]=current['display_name']
        normalized.append(current)
    result['places']=normalized

    def replace(value):
        if isinstance(value,str):
            for old,new in replacements.items():value=value.replace(old,new)
            return value
        if isinstance(value,list):return [replace(item) for item in value]
        if isinstance(value,dict):return {key:replace(item) for key,item in value.items()}
        return value

    # Place objects are already normalized; replace stale names in itinerary/module prose.
    for key in ('itinerary','module_groups'):
        if key in result:result[key]=replace(result[key])
    return result
