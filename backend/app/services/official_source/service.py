from __future__ import annotations
import json,os,uuid
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse
from .models import OfficialSourceCandidate,OfficialSourceDecision
from .resolver import OfficialSourceResolver

ELIGIBLE_HINTS=('博物馆','主题公园','景区','风景名胜','文化场馆','寺','园林','国家公园','动物园','纪念馆','遗址')

def _now():return datetime.now(timezone.utc)

class OfficialSourceDiscoveryService:
    def __init__(self,primary,*,connect=None,google_fallback=None,resolver=None):
        self.primary=primary;self.connect=connect;self.google_fallback=google_fallback
        self.resolver=resolver or OfficialSourceResolver();self.ttl=int(os.getenv('OFFICIAL_SOURCE_CACHE_TTL_SECONDS','7776000'))
    def eligible(self,place):
        if place.get('place_type')=='generated_experience' and not place.get('linked_place_id'):return False
        if place.get('type') in ('restaurant','chain'):return False
        category=str(place.get('category') or '')
        return place.get('place_type') in ('sight','transport') or any(value in category for value in ELIGIBLE_HINTS)
    def _cache(self,place_id):
        if not self.connect or not place_id:return None
        with self.connect() as db:
            row=db.execute("SELECT * FROM official_sources WHERE place_id=? AND status='verified_official' AND expires_at>? ORDER BY confidence DESC LIMIT 1",
                           (place_id,_now().isoformat())).fetchone()
        if not row:return None
        candidate=OfficialSourceCandidate(url=row['url'],domain=row['domain'],rank=row['search_rank'] or 1,
            provider='cache',query=row['discovery_query'] or 'cache')
        return OfficialSourceDecision(selected_url=row['url'],status='cache_hit',confidence=row['confidence'],
            selected_candidate=candidate,evidence=json.loads(row['evidence_json'] or '{}'))
    def _persist(self,place,decision):
        if not self.connect or not decision.selected_candidate:return
        item=decision.selected_candidate;stamp=_now();expires=stamp+timedelta(seconds=self.ttl)
        with self.connect() as db:
            db.execute("""INSERT INTO official_sources(place_id,url,domain,source_type,status,confidence,
              discovery_provider,discovery_query,search_rank,resolved_at,last_verified_at,expires_at,evidence_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(place_id,url) DO UPDATE SET status=excluded.status,
              confidence=excluded.confidence,discovery_provider=excluded.discovery_provider,discovery_query=excluded.discovery_query,
              search_rank=excluded.search_rank,resolved_at=excluded.resolved_at,expires_at=excluded.expires_at,evidence_json=excluded.evidence_json""",
              (place.get('id'),item.url,item.domain,'official_website_candidate',decision.status,decision.confidence,
               item.provider,item.query,item.rank,stamp.isoformat(),None,expires.isoformat(),json.dumps(decision.evidence,ensure_ascii=False)))
    def _log(self,place,provider,metric,status,error=None):
        if not self.connect:return
        with self.connect() as db:
            db.execute("""INSERT INTO official_search_logs(request_id,place_id,provider,query,status,result_count,
              latency_ms,cache_hit,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (str(uuid.uuid4()),place.get('id'),provider.name,metric.get('query',''),status,
               metric.get('result_count',0),metric.get('latency_ms',0),int(metric.get('cache_hit',False)),error,_now().isoformat()))
    async def discover(self,place):
        if not self.eligible(place):return OfficialSourceDecision(status='not_eligible',confidence=0,evidence={'reason':'place type not eligible'})
        cached=self._cache(place.get('id'))
        if cached:return cached
        # canonical_name is the authoritative identity established before enrichment.
        # A provider-local label can refer to a nearby or similarly named entity.
        name=place.get('canonical_name') or place.get('local_name');city=place.get('city') or ''
        queries=[f'{name} 官网',f'{name} {city} 官方网站'];all_candidates=[];best=None
        for query in queries:
            before=len(getattr(self.primary,'metrics',[]))
            try:candidates=await self.primary.search_official_source(place,query)
            except Exception as exc:
                self._log(place,self.primary,{'query':query},'provider_error',f'{type(exc).__name__}: {exc}');continue
            metric=(getattr(self.primary,'metrics',[])[-1] if len(getattr(self.primary,'metrics',[]))>before else {'query':query,'result_count':len(candidates)})
            all_candidates.extend(candidates);decision=self.resolver.resolve(place,all_candidates)
            self._log(place,self.primary,metric,decision.status)
            if best is None or decision.confidence>best.confidence:best=decision
            if decision.status=='verified_official':self._persist(place,decision);return decision
        if os.getenv('ENABLE_GOOGLE_OFFICIAL_FALLBACK','false').lower() in ('1','true','yes') and self.google_fallback:
            candidates=await self.google_fallback.search_official_source(place,queries[-1]);decision=self.resolver.resolve(place,candidates)
            if best is None or decision.confidence>best.confidence:best=decision
            if decision.status=='verified_official':self._persist(place,decision);return decision
        if best:self._persist(place,best);return best
        return OfficialSourceDecision(status='unverified',confidence=0,evidence={'reason':'no qualified search result'})
