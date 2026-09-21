"""Small async AMap Web Service client with persistent response cache."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime,timezone,timedelta
from typing import Callable

import httpx


def _now():return datetime.now(timezone.utc)


class AmapConfigurationError(RuntimeError):pass


class AmapProviderError(RuntimeError):
    def __init__(self,message: str,*,code: str='POI_PROVIDER_ERROR',status_code: int|None=None):
        self.code=code;self.status_code=status_code
        super().__init__(message)


class AmapClient:
    def __init__(self,key: str|None=None,base_url: str|None=None,connect: Callable|None=None,
                 transport: httpx.AsyncBaseTransport|None=None):
        configured=os.getenv('AMAP_API_KEY','') or os.getenv('AMAP_WEB_SERVICE_KEY','')
        self.key=(key if key is not None else configured).strip()
        if not self.key:raise AmapConfigurationError('AMAP_API_KEY / AMAP_WEB_SERVICE_KEY 未配置')
        self.base_url=(base_url or os.getenv('AMAP_BASE_URL','https://restapi.amap.com/v3')).rstrip('/')
        self.poi_base_url=os.getenv('AMAP_POI_BASE_URL','https://restapi.amap.com/v5').rstrip('/')
        self.connect=connect;self.transport=transport
        self.timeout=httpx.Timeout(connect=float(os.getenv('AMAP_CONNECT_TIMEOUT_SECONDS','10')),
                                   read=float(os.getenv('AMAP_READ_TIMEOUT_SECONDS','20')),
                                   write=10,pool=10)
        self.ttl=int(os.getenv('AMAP_CACHE_TTL_SECONDS','2592000'))

    def _cache_key(self,path: str,params: dict) -> str:
        safe={key:value for key,value in params.items() if key!='key'}
        raw=json.dumps({'path':path,'params':safe},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cached(self,operation: str,key: str):
        if not self.connect:return None
        with self.connect() as db:
            row=db.execute("SELECT payload FROM provider_cache WHERE provider='amap' AND operation=? AND cache_key=? AND expires_at>?",
                           (operation,key,_now().isoformat())).fetchone()
        return json.loads(row['payload']) if row else None

    def _save_cache(self,operation: str,key: str,payload: dict):
        if not self.connect:return
        created=_now();expires=created+timedelta(seconds=self.ttl)
        with self.connect() as db:
            db.execute("""INSERT INTO provider_cache(provider,operation,cache_key,payload,created_at,expires_at)
              VALUES('amap',?,?,?,?,?) ON CONFLICT(provider,operation,cache_key) DO UPDATE SET
              payload=excluded.payload,created_at=excluded.created_at,expires_at=excluded.expires_at""",
              (operation,key,json.dumps(payload,ensure_ascii=False),created.isoformat(),expires.isoformat()))

    async def get(self,path: str,params: dict,*,operation: str,base_url: str|None=None) -> tuple[dict,bool]:
        request_base=(base_url or self.base_url).rstrip('/')
        values={**params,'key':self.key,'output':'JSON'};cache_key=self._cache_key(request_base+path,values)
        cached=self._cached(operation,cache_key)
        if cached is not None:return cached,True
        try:
            async with httpx.AsyncClient(base_url=request_base,timeout=self.timeout,transport=self.transport) as client:
                response=await client.get(path,params=values)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AmapProviderError(f'高德 {operation} 请求超时',status_code=None) from exc
        except httpx.HTTPStatusError as exc:
            raise AmapProviderError(f'高德 {operation} HTTP {exc.response.status_code}',status_code=exc.response.status_code) from exc
        except httpx.RequestError as exc:
            raise AmapProviderError(f'高德 {operation} 连接失败',status_code=None) from exc
        try:data=response.json()
        except ValueError as exc:raise AmapProviderError(f'高德 {operation} 返回非 JSON') from exc
        if str(data.get('status'))!='1':
            info=str(data.get('info') or 'unknown error');code=str(data.get('infocode') or 'POI_PROVIDER_ERROR')
            raise AmapProviderError(f'高德 {operation} 返回错误：{info}',code=code,status_code=response.status_code)
        self._save_cache(operation,cache_key,data)
        return data,False
