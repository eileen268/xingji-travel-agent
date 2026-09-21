"""Minimal AMap POI health check. Never prints the API key."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));load_dotenv(ROOT/'.env')

from app.services.amap import AmapClient,search_pois


async def main():
    client=AmapClient()
    for query in ('正宁路夜市','甘肃省博物馆','中山桥'):
        values,cached=await search_pois(client,query,'兰州',3)
        top=values[0] if values else None
        print({'query':query,'success':bool(top),'cache_hit':cached,
               'provider_place_id':top.provider_place_id if top else None,
               'name':top.name if top else None,'city':top.city if top else None})


if __name__=='__main__':asyncio.run(main())
