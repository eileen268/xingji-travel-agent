"""Fetch raw AMap POI 2.0 business fields for four fixed Shanghai POIs."""
from __future__ import annotations

import asyncio,json,re,time
from datetime import datetime,timezone
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')

from app.services.amap import AmapClient,search_pois
from scripts.google_places_poc import connection

POIS=('上海博物馆','上海迪士尼乐园','东方明珠','豫园')

def identity(value):return re.sub(r'[\s\-—_·•（）()\[\]【】/\\.,，。]+','',str(value or '').casefold())

async def run():
    connect=connection();client=AmapClient(connect=connect);results=[]
    for name in POIS:
        started=time.perf_counter()
        candidates,search_cache_hit=await search_pois(client,name,'上海',20)
        exact=[item for item in candidates if identity(item.name)==identity(name)]
        selected=exact[0] if exact else (candidates[0] if candidates else None)
        if not selected:
            results.append({'poi':name,'status':'not_found','provider_place_id':None,'raw_business':None})
            continue
        data,detail_cache_hit=await client.get('/place/detail',
            {'id':selected.provider_place_id,'show_fields':'business'},operation='poi_detail_v2',base_url=client.poi_base_url)
        raw=(data.get('pois') or [{}])[0];business=raw.get('business') if isinstance(raw.get('business'),dict) else {}
        results.append({'poi':name,'status':'completed','matched_name':raw.get('name') or selected.name,
            'provider_place_id':selected.provider_place_id,'endpoint':'/v5/place/detail','show_fields':'business',
            'search_cache_hit':search_cache_hit,'detail_cache_hit':detail_cache_hit,
            'elapsed_ms':round((time.perf_counter()-started)*1000,1),'raw_business':business,
            'presence':{field:business.get(field) not in (None,'',[]) for field in
                ('opentime_today','opentime_week','tel','rating','cost','business_area','tag')}})
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'provider':'amap','api_version':'POI 2.0 (/v5)',
            'request_contract':{'show_fields':'business','detail_by_provider_place_id':True},'results':results}
    output=ROOT/'benchmark_runs/amap_business_poc.json';output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(run())
