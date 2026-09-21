"""Run the fixed Shanghai POI resolution regression without invoking an LLM."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));load_dotenv(ROOT/'.env')

from app.place_resolution import PlaceResolver,ResolutionContext


CASES=[
    ('上海博物馆','sight',ResolutionContext(
        next_coordinates=(31.220484,121.474465),day_cluster_coordinates=[(31.220484,121.474465)],
        area_labels=['人民广场','黄浦区'])),
    ('南翔馒头店','restaurant',ResolutionContext(
        previous_coordinates=(31.227714,121.492497),next_coordinates=(31.233516,121.492127),
        day_cluster_coordinates=[(31.227714,121.492497),(31.233516,121.492127)],area_labels=['豫园','外滩'])),
    ('外滩27号','sight',ResolutionContext(
        previous_coordinates=(31.233516,121.492127),day_cluster_coordinates=[(31.233516,121.492127)],
        area_labels=['外滩'])),
    ('海底捞','chain',ResolutionContext(
        previous_coordinates=(31.228231,121.475480),next_coordinates=(31.220484,121.474465),
        day_cluster_coordinates=[(31.228231,121.475480),(31.220484,121.474465)],area_labels=['人民广场','新天地'])),
    ('喜茶','chain',ResolutionContext(
        previous_coordinates=(31.228231,121.475480),next_coordinates=(31.220484,121.474465),
        day_cluster_coordinates=[(31.228231,121.475480),(31.220484,121.474465)],area_labels=['人民广场','新天地'])),
    ('上海老饭店','restaurant',ResolutionContext(
        previous_coordinates=(31.227714,121.492497),next_coordinates=(31.233516,121.492127),
        day_cluster_coordinates=[(31.227714,121.492497),(31.233516,121.492127)],area_labels=['豫园','外滩'])),
]


def place(name: str,place_type: str,index: int) -> dict:
    return {'id':f'llm_benchmark_{index}','type':place_type,'place_type':place_type,'city':'上海',
        'canonical_name':name,'local_name':name,'english_name':None,'display_name':name,
        'provider':'llm_generated','provider_place_id':None,'district':None,'address':None,
        'latitude':None,'longitude':None,'category':'','subcategory':None,'verification_status':'unverified',
        'source_confidence':0.0,'source_metadata':{},'fact_provenance':{},'semantic_tags':[]}


async def main():
    resolver=PlaceResolver()
    report=[]
    for index,(name,place_type,context) in enumerate(CASES,1):
        item=place(name,place_type,index);result=await resolver.resolve_place(item,context)
        report.append({'input_name':name,'queries':result.queries,'status':result.status,
            'confidence':result.confidence,'resolution_reason':result.resolution_reason,
            'selected':({'display_name':item.get('display_name'),'provider_place_id':item.get('provider_place_id'),
                         'district':item.get('district'),'address':item.get('address'),
                         'latitude':item.get('latitude'),'longitude':item.get('longitude')}
                        if result.selected_place_id else None),
            'candidates':[{'name':candidate.name,'provider_place_id':candidate.provider_place_id,
                           'district':candidate.district,'score':candidate.score,
                           'score_components':candidate.score_components}
                          for candidate in result.candidates]})
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
