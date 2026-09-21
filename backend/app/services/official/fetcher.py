from __future__ import annotations

import hashlib
import os
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse
from typing import Callable,Literal

import httpx
from pydantic import BaseModel,ConfigDict


def _now():return datetime.now(timezone.utc)


class FetchedOfficialPage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    page_type: Literal['homepage','opening_hours','ticket','reservation','visitor_notice']
    url: str
    final_url: str
    fetched_at: str
    http_status: int|None
    content_hash: str|None
    content: str|None
    cache_hit: bool=False
    error: str|None=None


class OfficialPageFetcher:
    def __init__(self,connect: Callable|None=None,transport: httpx.AsyncBaseTransport|None=None):
        self.connect=connect;self.transport=transport
        self.timeout=httpx.Timeout(
            connect=float(os.getenv('OFFICIAL_PAGE_CONNECT_TIMEOUT_SECONDS','10')),
            read=float(os.getenv('OFFICIAL_PAGE_READ_TIMEOUT_SECONDS','20')),
            write=float(os.getenv('OFFICIAL_PAGE_WRITE_TIMEOUT_SECONDS','10')),
            pool=float(os.getenv('OFFICIAL_PAGE_POOL_TIMEOUT_SECONDS','10')),
        )
        self.ttl=int(os.getenv('OFFICIAL_PAGE_CACHE_TTL_SECONDS','86400'))
        self.error_ttl=int(os.getenv('OFFICIAL_PAGE_ERROR_CACHE_TTL_SECONDS','600'))
        self.max_bytes=int(os.getenv('OFFICIAL_PAGE_MAX_BYTES','2000000'))

    def _cached(self,url):
        if not self.connect:return None
        with self.connect() as db:
            row=db.execute('SELECT * FROM official_page_cache WHERE url=? AND expires_at>?',(url,_now().isoformat())).fetchone()
        return dict(row) if row else None

    def _save(self,page: FetchedOfficialPage):
        if not self.connect:return
        expires=_now()+timedelta(seconds=self.error_ttl if page.error else self.ttl)
        with self.connect() as db:
            db.execute("""INSERT INTO official_page_cache
              (url,page_type,final_url,http_status,content_hash,content,fetched_at,expires_at,error)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET page_type=excluded.page_type,
              final_url=excluded.final_url,http_status=excluded.http_status,content_hash=excluded.content_hash,
              content=excluded.content,fetched_at=excluded.fetched_at,expires_at=excluded.expires_at,error=excluded.error""",
              (page.url,page.page_type,page.final_url,page.http_status,page.content_hash,page.content,page.fetched_at,
               expires.isoformat(),page.error))

    async def fetch(self,page_type: str,url: str,allowed_domains: list[str]) -> FetchedOfficialPage:
        host=(urlparse(url).hostname or '').lower()
        if urlparse(url).scheme!='https' or not any(host==domain or host.endswith('.'+domain) for domain in allowed_domains):
            raise ValueError('official page URL is outside resolved official domains')
        cached=self._cached(url)
        if cached:
            return FetchedOfficialPage(page_type=page_type,url=url,final_url=cached['final_url'],
                fetched_at=cached['fetched_at'],http_status=cached['http_status'],content_hash=cached['content_hash'],
                content=cached['content'],cache_hit=True,error=cached['error'])
        stamp=_now().isoformat()
        try:
            async with httpx.AsyncClient(timeout=self.timeout,transport=self.transport,follow_redirects=True,
                headers={'User-Agent':'JourneyNotesFactResolver/1.0 (+official-information-check)'}) as client:
                response=await client.get(url,headers={'Accept':'text/html,application/xhtml+xml'})
                response.raise_for_status()
                content=response.content[:self.max_bytes].decode(response.encoding or 'utf-8',errors='replace')
                final_host=(response.url.host or '').lower()
                if not any(final_host==domain or final_host.endswith('.'+domain) for domain in allowed_domains):
                    raise ValueError('official page redirected outside resolved official domains')
                page=FetchedOfficialPage(page_type=page_type,url=url,final_url=str(response.url),fetched_at=stamp,
                    http_status=response.status_code,content_hash=hashlib.sha256(content.encode()).hexdigest(),content=content)
        except (httpx.HTTPError,ValueError) as exc:
            status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
            page=FetchedOfficialPage(page_type=page_type,url=url,final_url=url,fetched_at=stamp,http_status=status,
                content_hash=None,content=None,error=f'{type(exc).__name__}: {exc}')
        self._save(page);return page
