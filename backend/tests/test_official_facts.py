import asyncio
import sqlite3
from contextlib import contextmanager

import httpx
import pytest

from app.migrations import migrate
from app.models import ResolvedPlaceFact
from app.services.official import (FactCandidate,FactExtractor,FactMerger,OfficialFactService,
                                   OfficialPageFetcher,OfficialSourceResolver)


@pytest.mark.parametrize('name,expected_domain',[
    ('上海博物馆','shanghaimuseum.cn'),('上海迪士尼乐园','shanghaidisneyresort.com'),
    ('东方明珠','orientalpearltower.com'),('豫园','yugarden.com.cn'),
])
def test_fixed_pois_resolve_to_reviewed_official_domains(name,expected_domain):
    result=OfficialSourceResolver().resolve({'id':'p','canonical_name':name,'city':'上海','category':'风景名胜'})
    assert result.status=='resolved' and result.confidence>=.85
    assert expected_domain in result.official_url
    assert result.resolution_method=='curated_registry_name_city_category'


def local_db(tmp_path):
    path=tmp_path/'official.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    return connect


def test_fetch_extract_and_cache_official_page(tmp_path):
    calls=[]
    html='''<html><body><h1>豫园参观信息</h1><p>开放时间 9:00—16:30，16:00停止入园。</p>
      <p>游客须通过上海豫园官方小程序预约购票。</p><p>票价以购票页面当日展示为准。</p>
      <p>联系电话 021-63260830。</p></body></html>'''
    async def handler(request):calls.append(request);return httpx.Response(200,text=html)
    fetcher=OfficialPageFetcher(local_db(tmp_path),httpx.MockTransport(handler))
    page=asyncio.run(fetcher.fetch('reservation','https://www.yugarden.com.cn/visit',['yugarden.com.cn']))
    facts=FactExtractor().extract(page,'https://www.yugarden.com.cn/')
    values={fact.fact_name:fact.value for fact in facts}
    assert values['opening_hours']=='9:00-16:30'
    assert values['reservation_required'] is True
    assert values['reservation_url']=='https://www.yugarden.com.cn/visit'
    assert values['phone']=='021-63260830'
    cached=asyncio.run(fetcher.fetch('reservation','https://www.yugarden.com.cn/visit',['yugarden.com.cn']))
    assert cached.cache_hit and len(calls)==1 and cached.content_hash==page.content_hash


def candidate(name,value,source,confidence=.9):
    return FactCandidate(fact_name=name,value=value,source_url='https://official.example/facts',
        source_type=source,extracted_at='2026-09-15T00:00:00+00:00',confidence=confidence,raw_evidence=str(value))


def test_official_fact_wins_conflict_without_discarding_amap_source():
    merged=FactMerger().merge([
        candidate('opening_hours','09:00-17:00','official_website'),
        candidate('opening_hours','08:30-18:00','amap',.65),
    ],['opening_hours'])['opening_hours']
    assert merged['value']=='09:00-17:00' and merged['status']=='conflicting'
    assert [source['source_type'] for source in merged['sources']]==['official_website','amap']
    assert 'fact_name' not in merged
    ResolvedPlaceFact.model_validate(merged)


def test_amap_weekly_business_hours_are_provider_verified_and_traceable():
    place={'id':'amap-place','provider_place_id':'B0TEST','map_url':'https://www.amap.com/search?id=B0TEST',
           'source_metadata':{'amap_queried_at':'2026-09-15T00:00:00+00:00',
             'amap_opening_hours_today':'09:00-17:00','amap_opening_hours_weekly':'周二至周日 09:00-17:00',
             'amap_phone':'021-12345678','amap_provider_rating':4.8,'amap_provider_cost':80.0}}
    service=OfficialFactService();facts=service.merger.merge(service._amap_candidates(place),
        ['opening_hours','phone','provider_rating','provider_cost'])
    hours=facts['opening_hours']
    assert hours['value']=={'today':'09:00-17:00','weekly':'周二至周日 09:00-17:00'}
    assert hours['status']=='provider_verified' and hours['source_type']=='amap'
    assert hours['provider_place_id']=='B0TEST'
    assert facts['provider_rating']['value']==4.8 and facts['provider_cost']['value']==80.0
    ResolvedPlaceFact.model_validate(hours)


def test_amap_hours_replace_unverified_display_note():
    place={'id':'amap-place','canonical_name':'测试景点','display_name':'测试景点','city':'上海','category':'景点',
           'provider_place_id':'B0TEST','map_url':'https://www.amap.com/search?id=B0TEST','official_url':None,
           'hours_note':'开放时间尚未核实，出发前请复核。','fact_provenance':{},
           'source_metadata':{'amap_queried_at':'2026-09-15T00:00:00+00:00',
             'amap_opening_hours_today':'09:00-17:00','amap_opening_hours_weekly':'周一至周日 09:00-17:00'}}
    asyncio.run(OfficialFactService().resolve_place(place))
    assert place['official_facts']['opening_hours']['status']=='provider_verified'
    assert place['hours_note']=='周一至周日 09:00-17:00'


def test_unresolved_source_does_not_invent_facts():
    place={'id':'unknown','canonical_name':'不存在的测试地点','display_name':'不存在的测试地点','city':'上海',
           'category':'','source_metadata':{},'fact_provenance':{},'map_url':None,'official_url':None}
    warnings=asyncio.run(OfficialFactService().resolve_place(place))
    assert not warnings and place['official_url'] is None
    assert all(fact['status']=='unavailable' and fact['value'] is None for fact in place['official_facts'].values())


def test_official_service_merges_resolution_extraction_and_provenance():
    html='<html><body>上海博物馆 开放时间 9:00-17:00，散客免预约。参观须知：16:00停止入场。</body></html>'
    async def handler(request):return httpx.Response(200,text=html)
    fetcher=OfficialPageFetcher(transport=httpx.MockTransport(handler))
    service=OfficialFactService(fetcher=fetcher)
    place={'id':'museum','canonical_name':'上海博物馆','display_name':'上海博物馆','city':'上海',
           'category':'博物馆','source_metadata':{},'fact_provenance':{},'map_url':None,'official_url':None}
    asyncio.run(service.resolve_place(place))
    assert place['official_url'].startswith('https://www.shanghaimuseum.cn/')
    assert place['official_facts']['opening_hours']['status']=='verified'
    assert place['official_facts']['reservation_required']['value'] is False
    assert place['fact_provenance']['opening_hours']['source_type']=='official_website'
