"""Places API (New) client used only by the isolated PoC in this phase."""
from __future__ import annotations

import hashlib,json,os,time
from datetime import datetime,timezone,timedelta
from typing import Callable
import httpx


class GooglePlacesConfigurationError(RuntimeError):pass
class GooglePlacesProviderError(RuntimeError):
    def __init__(self,message,*,code='GOOGLE_PROVIDER_ERROR',status_code=None):
        self.code=code;self.status_code=status_code;super().__init__(message)


class GooglePlacesClient:
    SEARCH_MASK='places.id,places.displayName,places.formattedAddress,places.location,places.types,places.businessStatus'
    DETAILS_MASK=('id,displayName,formattedAddress,location,websiteUri,regularOpeningHours,'
                  'nationalPhoneNumber,internationalPhoneNumber,businessStatus,types')

    def __init__(self,key=None,base_url=None,connect: Callable|None=None,transport=None):
        self.key=(key if key is not None else os.getenv('GOOGLE_PLACES_API_KEY','')).strip()
        if not self.key:raise GooglePlacesConfigurationError('GOOGLE_PLACES_API_KEY 未配置')
        self.base_url=(base_url or os.getenv('GOOGLE_PLACES_BASE_URL','https://places.googleapis.com/v1')).rstrip('/')
        self.connect=connect;self.transport=transport
        self.timeout=httpx.Timeout(connect=float(os.getenv('GOOGLE_PLACES_CONNECT_TIMEOUT_SECONDS','10')),
            read=float(os.getenv('GOOGLE_PLACES_READ_TIMEOUT_SECONDS','25')),write=10,pool=10)
        self.ttl=int(os.getenv('GOOGLE_PLACES_CACHE_TTL_SECONDS','2592000'));self.metrics=[]

    def _key(self,method,path,body,mask):
        raw=json.dumps({'method':method,'path':path,'body':body,'mask':mask},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cached(self,operation,key):
        if not self.connect:return None
        with self.connect() as db:
            row=db.execute("SELECT payload FROM provider_cache WHERE provider='google_places' AND operation=? AND cache_key=? AND expires_at>?",
                           (operation,key,datetime.now(timezone.utc).isoformat())).fetchone()
        return json.loads(row['payload']) if row else None

    def _save(self,operation,key,payload):
        if not self.connect:return
        stamp=datetime.now(timezone.utc);expires=stamp+timedelta(seconds=self.ttl)
        with self.connect() as db:
            db.execute("""INSERT INTO provider_cache(provider,operation,cache_key,payload,created_at,expires_at)
              VALUES('google_places',?,?,?,?,?) ON CONFLICT(provider,operation,cache_key) DO UPDATE SET
              payload=excluded.payload,created_at=excluded.created_at,expires_at=excluded.expires_at""",
              (operation,key,json.dumps(payload,ensure_ascii=False),stamp.isoformat(),expires.isoformat()))

    async def request(self,method,path,*,operation,field_mask,body=None,params=None):
        cache_key=self._key(method,path,body or params or {},field_mask);cached=self._cached(operation,cache_key)
        if cached is not None:
            self.metrics.append({'operation':operation,'latency_ms':0,'cache_hit':True,'http_status':200});return cached,True
        started=time.perf_counter();status=None
        try:
            async with httpx.AsyncClient(base_url=self.base_url,timeout=self.timeout,transport=self.transport) as client:
                response=await client.request(method,path,json=body,params=params,headers={
                    'Content-Type':'application/json','X-Goog-Api-Key':self.key,'X-Goog-FieldMask':field_mask})
                status=response.status_code;response.raise_for_status();data=response.json()
        except httpx.TimeoutException as exc:raise GooglePlacesProviderError('Google Places 请求超时',status_code=status) from exc
        except httpx.HTTPStatusError as exc:raise GooglePlacesProviderError(f'Google Places HTTP {exc.response.status_code}',status_code=exc.response.status_code) from exc
        except (httpx.RequestError,ValueError) as exc:raise GooglePlacesProviderError(f'Google Places 请求失败：{type(exc).__name__}',status_code=status) from exc
        finally:
            self.metrics.append({'operation':operation,'latency_ms':round((time.perf_counter()-started)*1000,1),
                                 'cache_hit':False,'http_status':status})
        self._save(operation,cache_key,data);return data,False
