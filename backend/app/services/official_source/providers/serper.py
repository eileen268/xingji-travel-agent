from __future__ import annotations
import hashlib,json,os,time
from datetime import datetime,timezone,timedelta
from typing import Callable
from urllib.parse import urlparse
import httpx
from ..models import OfficialSourceCandidate

class SerperConfigurationError(RuntimeError):pass
class SerperProviderError(RuntimeError):
    def __init__(self,message,*,status_code=None):self.status_code=status_code;super().__init__(message)

class SerperOfficialSourceProvider:
    name='serper'
    def __init__(self,key=None,base_url=None,connect: Callable|None=None,transport=None):
        self.key=(key if key is not None else os.getenv('SERPER_API_KEY','')).strip()
        if not self.key:raise SerperConfigurationError('SERPER_API_KEY 未配置')
        self.base_url=(base_url or os.getenv('SERPER_BASE_URL','https://google.serper.dev')).rstrip('/')
        self.connect=connect;self.transport=transport
        self.timeout=httpx.Timeout(float(os.getenv('SERPER_TIMEOUT_SECONDS','12')))
        self.max_results=max(1,min(int(os.getenv('SERPER_MAX_RESULTS','5')),10))
        self.ttl=int(os.getenv('SERPER_CACHE_TTL_SECONDS','2592000'));self.metrics=[]
    def _cache_key(self,query):return hashlib.sha256(json.dumps({'q':query,'gl':'cn','hl':'zh-cn','num':self.max_results},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    def _cached(self,key):
        if not self.connect:return None
        with self.connect() as db:
            row=db.execute("SELECT payload FROM provider_cache WHERE provider='serper' AND operation='official_search' AND cache_key=? AND expires_at>?",(key,datetime.now(timezone.utc).isoformat())).fetchone()
        return json.loads(row['payload']) if row else None
    def _save(self,key,payload):
        if not self.connect:return
        stamp=datetime.now(timezone.utc);expires=stamp+timedelta(seconds=self.ttl)
        with self.connect() as db:
            db.execute("""INSERT INTO provider_cache(provider,operation,cache_key,payload,created_at,expires_at)
              VALUES('serper','official_search',?,?,?,?) ON CONFLICT(provider,operation,cache_key) DO UPDATE SET
              payload=excluded.payload,created_at=excluded.created_at,expires_at=excluded.expires_at""",
              (key,json.dumps(payload,ensure_ascii=False),stamp.isoformat(),expires.isoformat()))
    async def search_official_source(self,place: dict,query: str) -> list[OfficialSourceCandidate]:
        key=self._cache_key(query);data=self._cached(key);cached=data is not None;started=time.perf_counter();status=200 if cached else None
        if data is None:
            try:
                async with httpx.AsyncClient(base_url=self.base_url,timeout=self.timeout,transport=self.transport) as client:
                    response=await client.post('/search',json={'q':query,'gl':'cn','hl':'zh-cn','num':self.max_results},headers={'X-API-KEY':self.key,'Content-Type':'application/json'})
                    status=response.status_code;response.raise_for_status();data=response.json()
            except httpx.TimeoutException as exc:raise SerperProviderError('Serper 搜索超时') from exc
            except httpx.HTTPStatusError as exc:raise SerperProviderError(f'Serper HTTP {exc.response.status_code}',status_code=exc.response.status_code) from exc
            except (httpx.RequestError,ValueError) as exc:raise SerperProviderError(f'Serper 搜索失败：{type(exc).__name__}') from exc
            self._save(key,data)
        organic=list(data.get('organic') or [])[:self.max_results]
        self.metrics.append({'query':query,'latency_ms':0 if cached else round((time.perf_counter()-started)*1000,1),'cache_hit':cached,'http_status':status,'result_count':len(organic)})
        results=[]
        for index,item in enumerate(organic,1):
            url=str(item.get('link') or '').strip();host=(urlparse(url).hostname or '').lower()
            if url.startswith(('http://','https://')) and host:
                results.append(OfficialSourceCandidate(url=url,title=item.get('title'),snippet=item.get('snippet'),domain=host.removeprefix('www.'),rank=int(item.get('position') or index),provider='serper',query=query))
        return results
