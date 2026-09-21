"""Minimal route health check using the same client and parser as production."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));load_dotenv(ROOT/'.env')

from app.services.amap import AmapClient,AmapRouteService


async def main():
    origin={'id':'health-origin','display_name':'中山桥','latitude':36.066383,'longitude':103.821003}
    destination={'id':'health-destination','display_name':'甘肃省博物馆','latitude':36.066228,'longitude':103.774887}
    service=AmapRouteService(AmapClient())
    for mode in ('walking','driving','transit'):
        result=await service.route(origin,destination,mode,'兰州')
        print({'mode':mode,'status':result.verification_status,'provider':result.provider,
               'distance_meters':result.distance_meters,'duration_seconds':result.duration_seconds})


if __name__=='__main__':asyncio.run(main())
