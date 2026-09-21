"""Run the isolated 20-POI Google Places enrichment proof of concept."""
from __future__ import annotations

import argparse,asyncio,json,os,re,sqlite3,sys
from contextlib import contextmanager
from datetime import datetime,timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')

from app.migrations import migrate
from app.services.amap import AmapClient,search_pois
from app.services.google_places import GooglePlacesClient,GooglePlacesConfigurationError,GooglePlaceResolver


POIS=[
 ('故宫博物院','北京','博物馆'),('天坛公园','北京','公园'),('颐和园','北京','风景名胜'),
 ('上海博物馆','上海','博物馆'),('上海迪士尼乐园','上海','主题公园'),('东方明珠','上海','风景名胜'),('豫园','上海','园林'),
 ('秦始皇帝陵博物院','西安','博物馆'),('陕西历史博物馆','西安','博物馆'),('大唐不夜城','西安','风景名胜'),
 ('甘肃省博物馆','兰州','博物馆'),('中山桥','兰州','风景名胜'),('莫高窟','敦煌','风景名胜'),('鸣沙山月牙泉','敦煌','风景名胜'),
 ('西湖','杭州','风景名胜'),('灵隐寺','杭州','寺庙'),('拙政园','苏州','园林'),('苏州博物馆','苏州','博物馆'),
 ('成都大熊猫繁育研究基地','成都','动物园'),('张家界国家森林公园','张家界','国家公园'),
]

def _name(value):return re.sub(r'[\s·•（）()\-—_]','',str(value or '')).casefold()

def connection():
    path=Path(os.getenv('DATABASE_PATH',str(ROOT/'data/travel.db')))
    if not path.is_absolute():path=ROOT/path
    migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path,timeout=15);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    return connect

async def amap_place(client,name,city,category):
    candidates,_=await search_pois(client,name,city,5)
    if not candidates:return {'canonical_name':name,'local_name':name,'city':city,'category':category,
                              'address':None,'latitude':None,'longitude':None,'place_type':'sight'},None
    selected=max(candidates,key=lambda c:SequenceMatcher(None,_name(name),_name(c.name)).ratio())
    return {'canonical_name':name,'local_name':selected.name,'city':city,'district':selected.district,
            'category':selected.category or category,'address':selected.address,'latitude':selected.latitude,
            'longitude':selected.longitude,'place_type':'sight','provider_place_id':selected.provider_place_id},selected

async def run(output: Path,limit: int):
    if not os.getenv('GOOGLE_PLACES_API_KEY','').strip():
        report={'generated_at':datetime.now(timezone.utc).isoformat(),'execution_status':'configuration_missing',
                'error':'GOOGLE_PLACES_API_KEY 未配置','poc_size':min(limit,len(POIS)),'results':[]}
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2));return 2
    connect=connection();amap=AmapClient(connect=connect);google=GooglePlacesClient(connect=connect);resolver=GooglePlaceResolver(google)
    results=[]
    for expected_name,city,category in POIS[:limit]:
        try:
            canonical,amap_selected=await amap_place(amap,expected_name,city,category)
            resolution=await resolver.resolve(canonical);selected=resolution.selected
            results.append({'expected_name':expected_name,'city':city,
                'amap_match':amap_selected.name if amap_selected else None,
                'google_match':selected.display_name if selected else None,
                'google_place_id':selected.google_place_id if selected else None,
                'resolution_status':resolution.status,'resolution_score':resolution.score,
                'website_uri':selected.website_uri if selected else None,
                'opening_hours_present':bool(selected and selected.regular_opening_hours),
                'phone_present':bool(selected and (selected.national_phone_number or selected.international_phone_number)),
                'candidates':resolution.candidates,'error':None})
        except Exception as exc:
            results.append({'expected_name':expected_name,'city':city,'resolution_status':'provider_error',
                            'resolution_score':0,'website_uri':None,'opening_hours_present':False,
                            'phone_present':False,'error':f'{type(exc).__name__}: {exc}'})
    matched=[r for r in results if r['resolution_status'] in ('exact_match','probable_match')]
    network=[m for m in google.metrics if not m['cache_hit']]
    search_count=sum(m['operation']=='text_search' for m in network);details_count=sum(m['operation']=='place_details' for m in network)
    metrics={'poc_size':len(results),'google_place_match_count':len(matched),
      'google_exact_match_count':sum(r['resolution_status']=='exact_match' for r in results),
      'probable_match_count':sum(r['resolution_status']=='probable_match' for r in results),
      'ambiguous_count':sum(r['resolution_status']=='ambiguous' for r in results),
      'no_match_count':sum(r['resolution_status']=='no_match' for r in results),
      'website_uri_count':sum(bool(r.get('website_uri')) for r in matched),
      'opening_hours_count':sum(r.get('opening_hours_present',False) for r in matched),
      'phone_count':sum(r.get('phone_present',False) for r in matched),
      'google_network_request_count':len(network),'google_cache_hit_count':sum(m['cache_hit'] for m in google.metrics),
      'average_network_latency_ms':round(sum(m['latency_ms'] for m in network)/len(network),1) if network else 0,
      'estimated_list_cost_usd':round(search_count*.032+details_count*.020,3),
      'wrong_binding_count':'manual_review_required'}
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'execution_status':'completed',
      'production_pipeline_connected':False,'field_masks':{'text_search':google.SEARCH_MASK,'place_details':google.DETAILS_MASK},
      'billing_note':'估算按 Text Search Pro $32/1000 与 Place Details Enterprise $20/1000；实际受每月免费额度和账户定价影响。',
      'metrics':metrics,'request_metrics':google.metrics,'results':results}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--output',type=Path,default=ROOT/'benchmark_runs/google_places_poc.json')
    args=parser.parse_args();raise SystemExit(asyncio.run(run(args.output,max(1,min(args.limit,20)))))
