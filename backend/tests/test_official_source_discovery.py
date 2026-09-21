import asyncio,sqlite3
from contextlib import contextmanager
import httpx

from app.migrations import migrate
from app.services.official_source import OfficialSourceResolver,OfficialSourceDiscoveryService
from app.services.official_source.models import OfficialSourceCandidate
from app.services.official_source.providers.serper import SerperOfficialSourceProvider

PLACE={'id':'sh-museum','canonical_name':'上海博物馆','local_name':'上海博物馆','city':'上海',
       'category':'博物馆','place_type':'sight','type':'sight'}

def local_db(tmp_path):
    path=tmp_path/'source.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    return connect

def candidate(url,title,rank=1,provider='serper'):
    return OfficialSourceCandidate(url=url,title=title,snippet='上海博物馆官方网站，版权所有',
        domain=url.split('/')[2].removeprefix('www.'),rank=rank,provider=provider,query='上海博物馆 官网')

def test_serper_normalizes_results_uses_one_request_and_does_not_leak_key():
    seen=[]
    async def handler(request):
        seen.append(request);return httpx.Response(200,json={'organic':[{'title':'上海博物馆','link':'https://www.shanghaimuseum.cn/','snippet':'官方网站','position':1}]})
    provider=SerperOfficialSourceProvider(key='secret-test',transport=httpx.MockTransport(handler))
    results=asyncio.run(provider.search_official_source(PLACE,'上海博物馆 官网'))
    assert len(results)==1 and results[0].domain=='shanghaimuseum.cn'
    assert seen[0].headers['x-api-key']=='secret-test' and 'secret-test' not in str(seen[0].url)
    assert provider.metrics[0]['result_count']==1 and not provider.metrics[0]['cache_hit']

def test_resolver_rejects_ota_and_selects_reviewed_official_domain():
    decision=OfficialSourceResolver().resolve(PLACE,[
        candidate('https://you.ctrip.com/sight/shanghai.html','上海博物馆门票攻略',1),
        candidate('https://www.shanghaimuseum.cn/','上海博物馆官方网站',2)])
    assert decision.status=='verified_official' and decision.selected_url=='https://www.shanghaimuseum.cn/'
    assert any(item['reason']=='third_party_or_content_platform' for item in decision.rejected_candidates)

def test_resolver_rejects_video_guide_and_design_case_false_positives():
    decision=OfficialSourceResolver().resolve({'canonical_name':'中山桥','local_name':'中山桥','city':'兰州'},[
        candidate('https://www.youtube.com/watch?v=x','中山桥旅游视频',1),
        candidate('https://www.1983asia.com/case.php?id=44','大唐不夜城设计案例',2)])
    assert decision.status in ('unverified','third_party') and decision.selected_url is None

def test_resolver_rejects_user_hosted_page_even_when_title_looks_official():
    place={'canonical_name':'东方明珠','local_name':'东方明珠','city':'上海'}
    decision=OfficialSourceResolver().resolve(place,[
        candidate('https://sites.google.com/view/example','东方明珠官方网站',1)])
    assert decision.status=='third_party' and decision.selected_url is None

def test_canonical_name_prevents_similarly_named_provider_entity_from_verifying():
    place={**PLACE,'local_name':'上海自然博物馆'}
    candidate_value=candidate('https://www.snhm.org.cn/','上海自然博物馆首页',1)
    decision=OfficialSourceResolver().resolve(place,[candidate_value])
    assert decision.status!='verified_official'

class FakeProvider:
    name='serper'
    def __init__(self,results):self.results=results;self.calls=[];self.metrics=[]
    async def search_official_source(self,place,query):
        self.calls.append(query);result=self.results[len(self.calls)-1] if len(self.calls)<=len(self.results) else []
        self.metrics.append({'query':query,'result_count':len(result),'latency_ms':2,'cache_hit':False,'http_status':200})
        return result

def test_verified_first_query_stops_and_cache_prevents_later_search(tmp_path):
    connect=local_db(tmp_path);provider=FakeProvider([[candidate('https://www.shanghaimuseum.cn/','上海博物馆官方网站')]])
    service=OfficialSourceDiscoveryService(provider,connect=connect)
    first=asyncio.run(service.discover(PLACE));second=asyncio.run(service.discover(PLACE))
    assert first.status=='verified_official' and second.status=='cache_hit'
    assert len(provider.calls)==1
    with connect() as db:
        assert db.execute('select count(*) from official_search_logs').fetchone()[0]==1
        assert db.execute('select count(*) from official_sources').fetchone()[0]==1

def test_unqualified_results_use_at_most_two_queries_and_google_is_disabled(monkeypatch):
    provider=FakeProvider([[],[]]);fallback=FakeProvider([[candidate('https://example.com','上海博物馆')]])
    monkeypatch.setenv('ENABLE_GOOGLE_OFFICIAL_FALLBACK','false')
    result=asyncio.run(OfficialSourceDiscoveryService(provider,google_fallback=fallback).discover(PLACE))
    assert result.status=='unverified' and len(provider.calls)==2 and not fallback.calls

def test_restaurant_and_generated_experience_are_not_searched():
    provider=FakeProvider([]);service=OfficialSourceDiscoveryService(provider)
    restaurant={**PLACE,'id':'food','type':'restaurant','place_type':'restaurant'}
    generated={**PLACE,'id':'generated','place_type':'generated_experience','linked_place_id':None}
    assert asyncio.run(service.discover(restaurant)).status=='not_eligible'
    assert asyncio.run(service.discover(generated)).status=='not_eligible'
    assert not provider.calls
