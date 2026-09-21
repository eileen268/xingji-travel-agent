"""AMap route queries normalized into a cacheable backend-owned contract."""
from __future__ import annotations

import math
import os
from datetime import datetime,timezone,timedelta
from typing import Callable,Literal

from pydantic import BaseModel,ConfigDict,Field

from .client import AmapClient,AmapProviderError


def _now():return datetime.now(timezone.utc)


class RouteResult(BaseModel):
    model_config=ConfigDict(extra='forbid')
    origin_place_id: str
    destination_place_id: str
    mode: Literal['walking','driving','transit']
    distance_meters: int|None=Field(default=None,gt=0)
    duration_seconds: int|None=Field(default=None,gt=0)
    provider: Literal['amap','deterministic_estimate']
    verification_status: Literal['verified','estimated_by_distance','unresolved']
    provider_route_id: str|None=None
    queried_at: str
    cache_hit: bool=False
    warning: str|None=None
    origin_latitude: float|None=None
    origin_longitude: float|None=None
    destination_latitude: float|None=None
    destination_longitude: float|None=None
    http_status: int|None=None
    provider_response_code: str|None=None
    route_error: str|None=None


def _number(value):
    try:return int(round(float(value)))
    except (TypeError,ValueError):return None


def _haversine_meters(origin: dict,destination: dict) -> int|None:
    values=(origin.get('latitude'),origin.get('longitude'),destination.get('latitude'),destination.get('longitude'))
    if any(value is None for value in values):return None
    lat1,lng1,lat2,lng2=map(math.radians,map(float,values))
    a=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    return max(1,round(6371000*2*math.atan2(math.sqrt(a),math.sqrt(1-a))))


def estimate_route(origin: dict,destination: dict,mode: str,warning: str|None=None,*,route_error: str|None=None,
                   http_status: int|None=None,provider_response_code: str|None=None) -> RouteResult:
    straight=_haversine_meters(origin,destination);now=_now().isoformat()
    trace=dict(origin_latitude=origin.get('latitude'),origin_longitude=origin.get('longitude'),
        destination_latitude=destination.get('latitude'),destination_longitude=destination.get('longitude'),
        http_status=http_status,provider_response_code=provider_response_code)
    if straight is None:
        return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
            provider='deterministic_estimate',verification_status='unresolved',queried_at=now,
            warning=warning or '地点缺少坐标，无法计算路线。',route_error=route_error or 'ROUTE_INPUT_INVALID',**trace)
    factors={'walking':1.15,'driving':1.30,'transit':1.25};meters=max(1,round(straight*factors[mode]))
    # Conservative non-live estimates. Public transit includes access/wait overhead.
    seconds={'walking':round(meters/1.2),'driving':round(meters/7.0),'transit':round(meters/4.2)+480}[mode]
    return RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
        distance_meters=meters,duration_seconds=max(60,seconds),provider='deterministic_estimate',
        verification_status='estimated_by_distance',queried_at=now,
        warning=warning or '路线服务未返回结果，已按坐标距离保守估算。',route_error=route_error,**trace)


def _parse_route(data: dict,mode: str) -> tuple[int|None,int|None,str|None]:
    route=data.get('route') or {}
    values=route.get('transits') if mode=='transit' else route.get('paths')
    values=values if isinstance(values,list) else []
    options=[]
    for item in values:
        if not isinstance(item,dict):continue
        distance=_number(item.get('distance'));duration=_number(item.get('duration'))
        if duration is not None and distance is not None:options.append((duration,distance,str(item.get('strategy') or '') or None))
    if not options:return None,None,None
    duration,distance,route_id=min(options,key=lambda value:(value[0],value[1]))
    return distance,duration,route_id


class AmapRouteService:
    def __init__(self,client: AmapClient,connect: Callable|None=None):
        self.client=client;self.connect=connect
        self.ttl=int(os.getenv('AMAP_ROUTE_CACHE_TTL_SECONDS','604800'))
        self.fallback=os.getenv('AMAP_ROUTE_FALLBACK_ENABLED','1')!='0'

    def _cached(self,origin_id: str,destination_id: str,mode: str):
        if not self.connect:return None
        with self.connect() as db:
            row=db.execute("""SELECT * FROM routes WHERE origin_place_id=? AND destination_place_id=? AND mode=?
              AND expires_at>?""",(origin_id,destination_id,mode,_now().isoformat())).fetchone()
        if not row:return None
        if (origin_id!=destination_id and
                ((row['duration_seconds'] is not None and row['duration_seconds']<=0) or
                 (row['distance_meters'] is not None and row['distance_meters']<=0))):
            with self.connect() as db:db.execute(
                'DELETE FROM routes WHERE origin_place_id=? AND destination_place_id=? AND mode=?',(origin_id,destination_id,mode))
            return None
        return RouteResult(origin_place_id=row['origin_place_id'],destination_place_id=row['destination_place_id'],
            mode=row['mode'],distance_meters=row['distance_meters'],duration_seconds=row['duration_seconds'],
            provider=row['provider'],verification_status=row['verification_status'],provider_route_id=row['provider_route_id'],
            queried_at=row['queried_at'],cache_hit=True,warning=row['warning'],origin_latitude=row['origin_latitude'],
            origin_longitude=row['origin_longitude'],destination_latitude=row['destination_latitude'],
            destination_longitude=row['destination_longitude'],http_status=row['http_status'],
            provider_response_code=row['provider_response_code'],route_error=row['route_error'])

    def _persist(self,result: RouteResult):
        if not self.connect:return
        expires=_now()+timedelta(seconds=self.ttl)
        with self.connect() as db:
            db.execute("""INSERT INTO routes(origin_place_id,destination_place_id,mode,distance_meters,duration_seconds,
              provider,verification_status,provider_route_id,warning,queried_at,expires_at,origin_latitude,
              origin_longitude,destination_latitude,destination_longitude,http_status,provider_response_code,route_error)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(origin_place_id,destination_place_id,mode) DO UPDATE SET
              distance_meters=excluded.distance_meters,duration_seconds=excluded.duration_seconds,
              provider=excluded.provider,verification_status=excluded.verification_status,
              provider_route_id=excluded.provider_route_id,warning=excluded.warning,
              origin_latitude=excluded.origin_latitude,origin_longitude=excluded.origin_longitude,
              destination_latitude=excluded.destination_latitude,destination_longitude=excluded.destination_longitude,
              http_status=excluded.http_status,provider_response_code=excluded.provider_response_code,route_error=excluded.route_error,
              queried_at=excluded.queried_at,expires_at=excluded.expires_at""",
              (result.origin_place_id,result.destination_place_id,result.mode,result.distance_meters,result.duration_seconds,
               result.provider,result.verification_status,result.provider_route_id,result.warning,result.queried_at,expires.isoformat(),
               result.origin_latitude,result.origin_longitude,result.destination_latitude,result.destination_longitude,
               result.http_status,result.provider_response_code,result.route_error))

    async def route(self,origin: dict,destination: dict,mode: Literal['walking','driving','transit'],city: str) -> RouteResult:
        cached=self._cached(origin['id'],destination['id'],mode)
        if cached:return cached
        if any(place.get(field) is None for place in (origin,destination) for field in ('latitude','longitude')):
            result=estimate_route(origin,destination,mode);self._persist(result);return result
        point=lambda p:f"{p['longitude']},{p['latitude']}"
        paths={'walking':'/direction/walking','driving':'/direction/driving','transit':'/direction/transit/integrated'}
        params={'origin':point(origin),'destination':point(destination)}
        if mode=='transit':params.update(city=city,cityd=city,extensions='base')
        try:
            data,_=await self.client.get(paths[mode],params,operation=f'route_{mode}')
            distance,duration,route_id=_parse_route(data,mode)
            if distance is None or duration is None:raise AmapProviderError('高德未返回可用路线',code='ROUTE_NOT_FOUND')
            if origin['id']!=destination['id'] and duration<=0:
                raise AmapProviderError('高德返回了无效路线时长',code='INVALID_ROUTE_DURATION',status_code=200)
            if origin['id']!=destination['id'] and distance<=0:
                raise AmapProviderError('高德返回了无效路线距离',code='INVALID_ROUTE_DISTANCE',status_code=200)
            result=RouteResult(origin_place_id=origin['id'],destination_place_id=destination['id'],mode=mode,
                distance_meters=distance,duration_seconds=duration,provider='amap',verification_status='verified',
                provider_route_id=route_id,queried_at=_now().isoformat(),origin_latitude=origin.get('latitude'),
                origin_longitude=origin.get('longitude'),destination_latitude=destination.get('latitude'),
                destination_longitude=destination.get('longitude'),http_status=200,
                provider_response_code=str(data.get('infocode') or '') or None)
        except AmapProviderError as exc:
            if not self.fallback:raise
            result=estimate_route(origin,destination,mode,f'高德路线暂不可用（{exc.code}），已按坐标距离保守估算。',
                route_error=exc.code,http_status=exc.status_code)
        self._persist(result);return result
