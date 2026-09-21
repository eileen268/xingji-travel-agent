"""Bind model-proposed places to real AMap POIs without trusting model geography."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from datetime import datetime,timezone
from difflib import SequenceMatcher
from typing import Callable,Literal

from pydantic import BaseModel,ConfigDict,Field

from .place_names import normalize_place_names
from .services.amap import (AmapClient,AmapConfigurationError,AmapProviderError,AmapPoiCandidate,
                            get_poi_detail,search_pois)
from .validators import map_url


def _now():return datetime.now(timezone.utc).isoformat()


class ScoredCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider_place_id: str
    name: str
    city: str
    district: str|None=None
    address: str|None=None
    latitude: float|None=None
    longitude: float|None=None
    category: str=''
    subcategory: str|None=None
    score: float=Field(ge=0,le=1)
    score_components: dict[str,float]
    website: str|None=None
    phone: str|None=None
    opening_hours: str|None=None
    opening_hours_today: str|None=None
    opening_hours_weekly: str|None=None
    provider_rating: float|None=None
    provider_cost: float|None=None
    business_area: str|None=None
    business_tags: list[str]=Field(default_factory=list)
    aliases: list[str]=Field(default_factory=list)
    business: dict=Field(default_factory=dict)


class PlaceResolutionResult(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: Literal['exact_match','alias_match','context_resolved','probable_match','ambiguous','multiple_candidates','no_match','generated_experience','provider_error','provider_unconfigured']
    selected_place_id: str|None=None
    selected_provider_place_id: str|None=None
    confidence: float=Field(ge=0,le=1)
    candidates: list[ScoredCandidate]
    cache_hit: bool=False
    error_code: str|None=None
    queries: list[str]=Field(default_factory=list)
    resolution_reason: str|None=None


class ResolutionContext(BaseModel):
    model_config=ConfigDict(extra='forbid')
    previous_coordinates: tuple[float,float]|None=None  # latitude, longitude
    next_coordinates: tuple[float,float]|None=None
    day_cluster_coordinates: list[tuple[float,float]]=Field(default_factory=list)
    area_labels: list[str]=Field(default_factory=list)


SUFFIX_RE=re.compile(r'(景区|景点区|风景区|旅游区)$')
PUNCT_RE=re.compile(r'[\s\-—_·•（）()\[\]【】/\\.,，。]+')
BRANCH_RE=re.compile(r'[（(][^）)]*(?:店|馆|区|路|广场|中心)[）)]$')
BRAND_BRANCH_RE=re.compile(r'(?:旗舰店|总店|分店|门店|店)$')
EXPERIENCE_MARKER_RE=re.compile(r'(制作|体验|工坊|工作坊|课程|课堂|研学|workshop|class|experience)',re.IGNORECASE)

# Kept as plain data so benchmark tuning does not require rewriting the scorer.
RESOLUTION_WEIGHTS={
    'without_context':{'name':.35,'brand':.10,'city':.20,'district':.05,'category':.15,'semantic':.15},
    'with_context':{'name':.27,'brand':.10,'city':.15,'district':.08,'category':.12,'semantic':.05,'area':.11,'route':.12},
}


def normalize_name(value: str|None) -> str:
    value=PUNCT_RE.sub('',str(value or '').casefold())
    return SUFFIX_RE.sub('',value)


def _branchless(value: str|None) -> str:
    return normalize_name(BRANCH_RE.sub('',str(value or '').strip()))


def _place_aliases(place: dict) -> list[str]:
    """Return evidence already present in the record; never translate a name."""
    metadata=place.get('source_metadata') or {}
    raw=[place.get('local_name'),place.get('canonical_name'),place.get('display_name'),place.get('english_name')]
    raw.extend(place.get('aliases') or [])
    raw.extend(metadata.get('aliases') or [])
    aliases=[]
    for value in raw:
        value=str(value or '').strip()
        if not value:continue
        for candidate in (value,BRANCH_RE.sub('',value).strip()):
            if candidate and candidate not in aliases:aliases.append(candidate)
    return aliases


def build_search_queries(place: dict,context: ResolutionContext|None=None) -> list[str]:
    """Build a bounded exact -> district -> alias fallback chain."""
    names=_place_aliases(place)
    district=str(place.get('district') or '').strip()
    city=str(place.get('city') or '').strip()
    queries=[]
    def add(value):
        value=' '.join(str(value or '').split())
        if value and value not in queries:queries.append(value)
    for name in names:
        add(name)
        if district:add(f'{name} {district}')
    # Region is still sent separately to AMap; this last fallback helps names
    # whose provider record includes the city prefix.
    if names and city:add(f'{names[0]} {city}')
    return queries[:6]


def normalize_city(value: str|None) -> str:
    return re.sub(r'(市|地区|自治州)$','',str(value or '').strip())


def canonical_place_id(provider: str,identity: str) -> str:
    digest=hashlib.sha256(f'{provider}:{identity}'.encode()).hexdigest()[:24]
    return f'{provider}_{digest}'


def _category_score(place_type: str,category: str) -> float:
    if not category:return .35
    category=category.casefold()
    rules={
        'restaurant':('餐饮服务','餐厅','小吃','快餐'),
        'chain':('餐饮服务','餐厅','小吃','快餐'),
        'sight':('风景名胜','科教文化服务','博物馆','纪念馆','公园','体育休闲服务'),
        'experience':('体育休闲服务','科教文化服务','生活服务','餐饮服务','风景名胜'),
    }
    return 1.0 if any(word in category for word in rules.get(place_type,())) else .15


def _semantic_score(place: dict,candidate: AmapPoiCandidate) -> float:
    category=candidate.category
    score=_category_score(place.get('type',''),category)
    tags=place.get('semantic_tags') or []
    signals={
        'museum':('博物馆','科教文化服务'),'restaurant':('餐饮服务',),
        'natural_scenic':('风景名胜','公园'),'historic_site':('风景名胜','科教文化服务'),
        'food_experience':('餐饮服务','生活服务'),'urban_park':('公园','风景名胜'),
    }
    if any(any(word in category for word in signals.get(tag,())) for tag in tags):return 1.0
    return score


def _distance_score(point: tuple[float,float]|None,candidate: AmapPoiCandidate) -> float:
    if not point or candidate.latitude is None or candidate.longitude is None:return .5
    lat1,lng1,lat2,lng2=map(math.radians,(point[0],point[1],candidate.latitude,candidate.longitude))
    value=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    km=6371*2*math.atan2(math.sqrt(value),math.sqrt(max(1e-12,1-value)))
    return max(0.0,1.0-km/35.0)


def _candidate_distance_m(a: ScoredCandidate,b: ScoredCandidate) -> float:
    if None in (a.latitude,a.longitude,b.latitude,b.longitude):return float('inf')
    lat1,lng1,lat2,lng2=map(math.radians,(a.latitude,a.longitude,b.latitude,b.longitude))
    value=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    return 6371000*2*math.atan2(math.sqrt(value),math.sqrt(max(1e-12,1-value)))


def score_candidate(place: dict,candidate: AmapPoiCandidate,context: ResolutionContext|None=None) -> ScoredCandidate:
    generated=normalize_name(place.get('local_name') or place.get('display_name'))
    generated_aliases=[normalize_name(value) for value in _place_aliases(place)]
    real=normalize_name(candidate.name)
    provider_aliases=[part for value in candidate.aliases for part in str(value).split('|') if part]
    variants=[real,_branchless(candidate.name),*[normalize_name(value) for value in provider_aliases]]
    variants=list(dict.fromkeys(value for value in variants if value))
    city_prefix=normalize_name(normalize_city(place.get('city')))
    for prefix in (city_prefix,city_prefix+'市'):
        if prefix and real.startswith(prefix) and len(real)>len(prefix):variants.append(real[len(prefix):])
    name=max((SequenceMatcher(None,wanted,value).ratio() for wanted in generated_aliases for value in variants),default=0.0)
    entity_name=1.0 if any(wanted and any(wanted in value or value in wanted for value in variants)
                           for wanted in generated_aliases) else 0.0
    generated_brand=max((_branchless(value) for value in _place_aliases(place)),key=len,default='')
    real_brand=_branchless(candidate.name)
    brand=1.0 if generated_brand and real_brand and (generated_brand in real_brand or real_brand in generated_brand) else 0.0
    city=1.0 if normalize_city(candidate.city)==normalize_city(place.get('city')) else 0.0
    requested_district=normalize_name(place.get('district'))
    candidate_district=normalize_name(candidate.district)
    district=(1.0 if requested_district and candidate_district and
              (requested_district in candidate_district or candidate_district in requested_district)
              else (.5 if not requested_district else 0.0))
    category=_category_score(place.get('type',''),candidate.category)
    semantic=_semantic_score(place,candidate)
    area=route=.5
    if context:
        area_points=context.day_cluster_coordinates
        area=max((_distance_score(point,candidate) for point in area_points),default=.5)
        neighbor_scores=[_distance_score(point,candidate) for point in (context.previous_coordinates,context.next_coordinates) if point]
        route=sum(neighbor_scores)/len(neighbor_scores) if neighbor_scores else area
        weight=RESOLUTION_WEIGHTS['with_context']
        total=round(min(1.0,weight['name']*name+weight['brand']*brand+weight['city']*city+
            weight['district']*district+weight['category']*category+weight['semantic']*semantic+
            weight['area']*area+weight['route']*route),4)
    else:
        weight=RESOLUTION_WEIGHTS['without_context']
        total=round(min(1.0,weight['name']*name+weight['brand']*brand+weight['city']*city+
            weight['district']*district+weight['category']*category+weight['semantic']*semantic),4)
    return ScoredCandidate(**candidate.model_dump(exclude={'provider','typecode','adcode','citycode'}),score=total,
                           score_components={'name_similarity':round(name,4),'city_match':city,
                                             'district_match':district,'category_match':category,
                                             'semantic_match':semantic,'area_fit':round(area,4),
                                             'route_fit':round(route,4),'entity_name_match':entity_name,
                                             'brand_match':brand})


def _can_bind_experience(place: dict,candidate: ScoredCandidate) -> bool:
    """Require entity-level evidence before turning an experience concept into a POI.

    A restaurant whose menu resembles a workshop title is not evidence that the
    restaurant actually offers a bookable class. Exact/near-exact provider names
    remain bindable, as do provider records explicitly named as an activity.
    """
    if place.get('type')!='experience':return True
    generated_name=str(place.get('experience_title') or place.get('local_name') or place.get('display_name') or '')
    if not EXPERIENCE_MARKER_RE.search(generated_name):return True
    name_similarity=candidate.score_components.get('name_similarity',0)
    provider_names_activity=bool(EXPERIENCE_MARKER_RE.search(candidate.name))
    provider_category_is_activity=any(value in candidate.category for value in ('体育休闲服务','科教文化服务','生活服务'))
    return name_similarity>=.80 and (provider_names_activity or provider_category_is_activity)


def _unverified(place: dict,status: str,error_code: str|None=None):
    generated=place.get('type')=='experience'
    identity='|'.join(str(place.get(key) or '') for key in ('city','type','local_name','display_name'))
    place['id']=canonical_place_id('generated' if generated else 'llm',identity)
    place.update(provider='llm_generated',provider_place_id=None,district=None,address=None,latitude=None,longitude=None,
                 category='',subcategory=None,place_type='generated_experience' if generated else place.get('type'),
                 source_confidence=0.0,verification_status='generated' if generated else 'unverified')
    if generated:
        place.update(entity_kind='experience_concept',linked_place_id=None,booking_status='unverified',map_url=None)
    place['source_metadata']={**(place.get('source_metadata') or {}),'resolution_status':status,
                              'resolution_error_code':error_code}
    place['fact_provenance']={}


def _bind(place: dict,candidate: ScoredCandidate,status: str):
    place['id']=canonical_place_id('amap',candidate.provider_place_id)
    place.update(provider='amap',provider_place_id=candidate.provider_place_id,
        canonical_name=candidate.name,local_name=candidate.name,display_name=candidate.name,
        name_source='map_provider',district=candidate.district,address=candidate.address,
        latitude=candidate.latitude,longitude=candidate.longitude,category=candidate.category,
        subcategory=candidate.subcategory,place_type=place.get('type'),source_confidence=candidate.score,
        verification_status='verified' if status=='exact_match' else 'partially_verified',
        source_metadata={'resolution_status':status,'score_components':candidate.score_components,
                         'amap_website':candidate.website,'amap_phone':candidate.phone,
                         'amap_opening_hours':candidate.opening_hours,
                         'amap_opening_hours_today':candidate.opening_hours_today,
                         'amap_opening_hours_weekly':candidate.opening_hours_weekly,
                         'amap_provider_rating':candidate.provider_rating,
                         'amap_provider_cost':candidate.provider_cost,
                         'amap_business_area':candidate.business_area,
                         'amap_business_tags':candidate.business_tags,
                         'amap_business':candidate.business,'amap_queried_at':_now()},
        fact_provenance={key:{'source_type':'provider','provider':'amap','queried_at':_now(),'confidence':candidate.score}
                         for key in ('canonical_name','city','district','address','latitude','longitude','category')})
    if place.get('type')=='experience':place['entity_kind']='poi'
    place['map_url']=map_url(place['city'],candidate.name)


class PlaceResolver:
    def __init__(self,connect: Callable|None=None,job_id: str|None=None,pack_id: str|None=None,
                 client: AmapClient|None=None):
        self.connect=connect;self.job_id=job_id;self.pack_id=pack_id
        try:self.client=client or AmapClient(connect=connect)
        except AmapConfigurationError:self.client=None

    def _persist(self,place: dict,result: PlaceResolutionResult,generated_place_id: str,generated_name: str):
        if not self.connect:return
        with self.connect() as db:
            db.execute("""INSERT INTO place_resolutions
              (resolution_id,job_id,pack_id,generated_place_id,generated_name,city,selected_place_id,provider_place_id,score,status,candidates_json,error_code,created_at,queries_json,context_json,resolution_reason)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (str(uuid.uuid4()),self.job_id,self.pack_id,generated_place_id,generated_name,
               place.get('city',''),result.selected_place_id,result.selected_provider_place_id,result.confidence,result.status,
               json.dumps([item.model_dump(mode='json') for item in result.candidates],ensure_ascii=False),result.error_code,_now(),
               json.dumps(result.queries,ensure_ascii=False),
               json.dumps((place.get('source_metadata') or {}).get('resolution_context'),ensure_ascii=False),
               result.resolution_reason))
            db.execute("""INSERT INTO canonical_places
              (place_id,provider,provider_place_id,canonical_name,local_name,english_name,display_name,city,district,address,
               latitude,longitude,category,subcategory,place_type,verification_status,source_confidence,semantic_tags,
               interest_affinity,source_metadata,fact_provenance,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(place_id) DO UPDATE SET
               provider=excluded.provider,provider_place_id=excluded.provider_place_id,canonical_name=excluded.canonical_name,
               local_name=excluded.local_name,english_name=excluded.english_name,display_name=excluded.display_name,city=excluded.city,
               district=excluded.district,address=excluded.address,latitude=excluded.latitude,longitude=excluded.longitude,
               category=excluded.category,subcategory=excluded.subcategory,place_type=excluded.place_type,
               verification_status=excluded.verification_status,source_confidence=excluded.source_confidence,
               semantic_tags=excluded.semantic_tags,interest_affinity=excluded.interest_affinity,
               source_metadata=excluded.source_metadata,fact_provenance=excluded.fact_provenance,updated_at=excluded.updated_at""",
              (place['id'],place.get('provider','llm_generated'),place.get('provider_place_id'),place['canonical_name'],
               place.get('local_name'),place.get('english_name'),place['display_name'],place['city'],place.get('district'),
               place.get('address'),place.get('latitude'),place.get('longitude'),place.get('category',''),place.get('subcategory'),
               place.get('place_type') or place['type'],place['verification_status'],place.get('source_confidence'),
               json.dumps(place.get('semantic_tags',[]),ensure_ascii=False),json.dumps(place.get('interest_affinity',{}),ensure_ascii=False),
               json.dumps(place.get('source_metadata',{}),ensure_ascii=False),json.dumps(place.get('fact_provenance',{}),ensure_ascii=False),_now()))

    async def resolve_place(self,place: dict,context: ResolutionContext|None=None) -> PlaceResolutionResult:
        normalized=normalize_place_names(place);place.clear();place.update(normalized)
        generated_place_id=place['id']
        generated_name=place.get('experience_title') or place.get('display_name','')
        if not self.client:
            _unverified(place,'provider_unconfigured','POI_PROVIDER_ERROR')
            result=PlaceResolutionResult(status='provider_unconfigured',confidence=0,candidates=[],error_code='POI_PROVIDER_ERROR',
                resolution_reason='AMap client is not configured')
            self._persist(place,result,generated_place_id,generated_name);return result
        queries=build_search_queries(place,context)
        candidates_by_id={};cache_hit=True
        try:
            for query in queries:
                found,hit=await search_pois(self.client,query,str(place['city']))
                cache_hit=cache_hit and hit
                for candidate in found:candidates_by_id[candidate.provider_place_id]=candidate
                # A unique exact identity with usable coordinates needs no
                # broader alias query. Brand/branch results continue so the
                # route context can choose the correct outlet.
                exact=[candidate for candidate in candidates_by_id.values()
                       if normalize_name(candidate.name)==normalize_name(query)
                       and candidate.latitude is not None and candidate.longitude is not None]
                if len(exact)==1 and not BRANCH_RE.search(exact[0].name):break
            candidates=list(candidates_by_id.values())
        except AmapProviderError as exc:
            _unverified(place,'provider_error',exc.code)
            result=PlaceResolutionResult(status='provider_error',confidence=0,candidates=[],error_code=exc.code,
                queries=queries,resolution_reason=str(exc))
            self._persist(place,result,generated_place_id,generated_name);return result
        scored=sorted((score_candidate(place,item,context) for item in candidates),key=lambda item:item.score,reverse=True)
        top=scored[0] if scored else None;status='no_match';selected=None;reason='no candidates returned'
        gap=top.score-scored[1].score if top and len(scored)>1 else 1.0
        requested_names={normalize_name(value) for value in _place_aliases(place)}
        requested_primary=normalize_name(place.get('local_name') or place.get('display_name'))
        exact_matches=[item for item in scored if normalize_name(item.name) in requested_names]
        alias_matches=[item for item in scored if item.score_components.get('brand_match')==1.0]
        second=scored[1] if len(scored)>1 else None
        same_complex=bool(top and second and context and _candidate_distance_m(top,second)<=500 and
            requested_primary and requested_primary in normalize_name(top.name) and
            requested_primary in normalize_name(second.name))
        geo_advantage=(min(top.score_components.get('area_fit',.5),top.score_components.get('route_fit',.5))-
                       min(second.score_components.get('area_fit',.5),second.score_components.get('route_fit',.5))
                       if top and second else 1.0)
        district_advantage=(top.score_components.get('district_match',0)-second.score_components.get('district_match',0)
                            if top and second else 1.0)
        if top and len(exact_matches)==1 and exact_matches[0].provider_place_id==top.provider_place_id:
            status='exact_match';selected=top.model_copy(update={'score':max(top.score,.9)});reason='unique normalized provider name match'
        elif top and len(alias_matches)==1 and alias_matches[0].provider_place_id==top.provider_place_id and top.score>=.75:
            status='alias_match';selected=top;reason='unique alias or branchless brand match'
        elif top and same_complex:
            status='context_resolved';selected=top;reason='neighbor context selected a representative inside the same place complex'
        elif top and context and top.score>=.68 and (gap>=.025 or geo_advantage>=.08 or district_advantage>=.5):
            status='context_resolved';selected=top;reason='day geography or neighboring stops disambiguated the candidate'
        elif top and len(scored)>1 and top.score>=.62 and gap<.035:
            status='ambiguous';reason='top candidates remain too close after context scoring'
        elif top and top.score>=.86 and top.score_components.get('name_similarity',0)>=.72:
            status='exact_match';selected=top;reason='high-confidence name, city and category match'
        elif top and top.score>=.72 and top.score_components.get('name_similarity',0)>=.58:
            status='probable_match';selected=top;reason='single sufficiently strong provider candidate'
        elif top:
            reason='best candidate stayed below the safe binding threshold'
        if selected and not _can_bind_experience(place,selected):
            status='generated_experience';selected=None;reason='candidate does not prove a real bookable experience'
        elif not selected and place.get('type')=='experience':
            status='generated_experience';reason='no reliable real experience provider found'
        if selected:
            # Search establishes the identity; the v5 ID lookup is the
            # authoritative source for the requested business extension.
            try:
                detailed,_=await get_poi_detail(self.client,selected.provider_place_id)
                if detailed:
                    details=detailed.model_dump(exclude={'provider','provider_place_id','name','city','district','address',
                        'latitude','longitude','category','subcategory','typecode','adcode','citycode'})
                    selected=selected.model_copy(update=details)
            except AmapProviderError:
                pass
            _bind(place,selected,status)
        else:_unverified(place,status)
        if context:
            place['source_metadata']={**(place.get('source_metadata') or {}),'resolution_context':context.model_dump(mode='json')}
        result=PlaceResolutionResult(status=status,selected_place_id=place['id'] if selected else None,
            selected_provider_place_id=selected.provider_place_id if selected else None,
            confidence=selected.score if selected else (top.score if top else 0),candidates=scored[:5],cache_hit=cache_hit,
            queries=queries,resolution_reason=reason)
        self._persist(place,result,generated_place_id,generated_name);return result

    async def resolve_places(self,places: list[dict],pack_id: str|None=None) -> tuple[list[PlaceResolutionResult],list[dict]]:
        self.pack_id=pack_id or self.pack_id;semaphore=asyncio.Semaphore(4)
        async def one(place):
            async with semaphore:return await self.resolve_place(place)
        results=await asyncio.gather(*(one(place) for place in places))
        warnings=[]
        for place,result in zip(places,results):
            if result.status in ('provider_error','provider_unconfigured'):
                warnings.append({'pointer':f'/places/{place["id"]}','code':'POI_PROVIDER_ERROR',
                    'message':'真实地点服务暂时不可用，已保留候选并标记待核验。'})
            elif result.status in ('no_match','multiple_candidates','ambiguous') and place.get('type')!='experience':
                warnings.append({'pointer':f'/places/{place["id"]}','code':'POI_NOT_FOUND' if result.status=='no_match' else 'POI_AMBIGUOUS',
                    'message':'地点暂未可靠绑定高德 POI，未作为已核验事实。'})
            elif result.status=='generated_experience':
                warnings.append({'pointer':f'/places/{place["id"]}','code':'UNVERIFIED_EXPERIENCE',
                    'message':'体验未找到可靠真实提供方，已作为生成型体验保存。'})
        return results,warnings
