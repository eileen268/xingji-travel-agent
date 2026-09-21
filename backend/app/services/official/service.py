from __future__ import annotations

import asyncio
import os
import json
from datetime import datetime,timezone
from typing import Callable

from .extractor import FactCandidate,FactExtractor
from .fetcher import OfficialPageFetcher
from .merger import FactMerger
from .resolver import OfficialSourceResolver


FACT_NAMES=('opening_hours','ticket_info','reservation_required','reservation_url','official_url','phone',
            'provider_rating','provider_cost','important_notice')


class OfficialFactService:
    def __init__(self,connect: Callable|None=None,job_id: str|None=None,fetcher: OfficialPageFetcher|None=None):
        self.connect=connect;self.job_id=job_id;self.resolver=OfficialSourceResolver(connect)
        self.fetcher=fetcher or OfficialPageFetcher(connect);self.extractor=FactExtractor();self.merger=FactMerger()

    def _amap_candidates(self,place):
        meta=place.get('source_metadata') or {};items=[];stamp=meta.get('amap_queried_at') or ''
        common={'source_url':place.get('map_url') or 'https://www.amap.com/','source_type':'amap',
                'extracted_at':stamp or 'unknown','provider_place_id':place.get('provider_place_id')}
        today=meta.get('amap_opening_hours_today');weekly=meta.get('amap_opening_hours_weekly')
        if today or weekly:
            value={'today':today,'weekly':weekly}
            items.append(FactCandidate(fact_name='opening_hours',value=value,confidence=.8,
                raw_evidence=json.dumps(value,ensure_ascii=False),**common))
        elif meta.get('amap_opening_hours'):
            items.append(FactCandidate(fact_name='opening_hours',value=str(meta['amap_opening_hours']),confidence=.65,
                raw_evidence=str(meta['amap_opening_hours']),**common))
        for fact,key,confidence in (('phone','amap_phone',.75),('provider_rating','amap_provider_rating',.8),
                                    ('provider_cost','amap_provider_cost',.75)):
            if meta.get(key) not in (None,''):
                items.append(FactCandidate(fact_name=fact,value=meta[key],confidence=confidence,
                                           raw_evidence=str(meta[key]),**common))
        return items

    async def resolve_place(self,place: dict) -> list[dict]:
        resolution=self.resolver.resolve(place,self.job_id);warnings=[];candidates=self._amap_candidates(place)
        if resolution.status=='resolved':
            stamp=datetime.now(timezone.utc).isoformat()
            candidates.append(FactCandidate(fact_name='official_url',value=resolution.official_url,
                source_url=resolution.official_url,source_type='official_website',extracted_at=stamp,
                confidence=resolution.confidence,raw_evidence=resolution.resolution_method))
            types_by_url={}
            for page_type,url in resolution.pages.items():types_by_url.setdefault(url,[]).append(page_type)
            fetched=await asyncio.gather(*(self.fetcher.fetch(kinds[0],url,resolution.allowed_domains)
                                           for url,kinds in types_by_url.items()))
            for base_page,(url,kinds) in zip(fetched,types_by_url.items()):
                for kind in kinds:
                    page=base_page.model_copy(update={'page_type':kind})
                    if page.error:
                        warnings.append({'pointer':f"/places/{place['id']}/official_facts",'code':'OFFICIAL_PAGE_UNAVAILABLE',
                                         'message':'已解析官网，但部分官方页面暂时无法读取，相关事实保持待复核。'})
                    candidates.extend(self.extractor.extract(page,resolution.official_url))
            place['official_url']=resolution.official_url
            place.setdefault('source_metadata',{})['official_source_resolution']={
                'source_type':resolution.source_type,'confidence':resolution.confidence,
                'resolution_method':resolution.resolution_method}
        elif resolution.status=='low_confidence':
            warnings.append({'pointer':f"/places/{place['id']}/official_url",'code':'OFFICIAL_SOURCE_LOW_CONFIDENCE',
                             'message':'发现可能的官网候选，但身份匹配不足，未自动绑定。'})
        place['official_facts']=self.merger.merge(candidates,FACT_NAMES)
        hours=place['official_facts'].get('opening_hours') or {}
        if hours.get('value') not in (None,''):
            value=hours['value'];display=(value.get('weekly') or value.get('today')) if isinstance(value,dict) else str(value)
            if display:
                suffix='；不同来源营业时间存在差异，请以官方最新信息为准。' if hours.get('status')=='conflicting' else ''
                place['hours_note']=str(display)+suffix
        for name,fact in place['official_facts'].items():
            place.setdefault('fact_provenance',{})[name]=fact
        if self.connect:
            with self.connect() as db:
                db.execute("UPDATE canonical_places SET official_facts=?,fact_provenance=?,updated_at=? WHERE place_id=?",
                           (json.dumps(place['official_facts'],ensure_ascii=False),json.dumps(place.get('fact_provenance',{}),ensure_ascii=False),
                            datetime.now(timezone.utc).isoformat(),place['id']))
        return warnings

    async def resolve_places(self,places: list[dict]) -> list[dict]:
        if os.getenv('OFFICIAL_FACTS_ENABLED','1').lower() in ('0','false','no'):return []
        semaphore=asyncio.Semaphore(int(os.getenv('OFFICIAL_FACTS_CONCURRENCY','3')))
        async def one(place):
            async with semaphore:return await self.resolve_place(place)
        groups=await asyncio.gather(*(one(place) for place in places))
        return [warning for group in groups for warning in group]
