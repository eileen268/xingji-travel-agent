from __future__ import annotations

import json
import re
import uuid
from datetime import datetime,timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse
from typing import Callable,Literal

from pydantic import BaseModel,ConfigDict,Field

from .registry import OFFICIAL_SOURCE_REGISTRY


def _now():return datetime.now(timezone.utc).isoformat()
def _name(value):return re.sub(r'[\s·•（）()\-—_]+','',str(value or '')).casefold()
def _city(value):return re.sub(r'(市|地区|自治州)$','',str(value or '').strip())


class OfficialSourceResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: Literal['resolved','low_confidence','unresolved']
    official_url: str|None=None
    source_type: Literal['official_website','provider_candidate','none']='none'
    confidence: float=Field(ge=0,le=1)
    resolution_method: str
    pages: dict[str,str]=Field(default_factory=dict)
    allowed_domains: list[str]=Field(default_factory=list)


class OfficialSourceResolver:
    AUTO_BIND_THRESHOLD=.85

    def __init__(self,connect: Callable|None=None,registry=OFFICIAL_SOURCE_REGISTRY):
        self.connect=connect;self.registry=registry

    def _score(self,place,entry):
        generated=_name(place.get('canonical_name') or place.get('display_name'))
        name=max((SequenceMatcher(None,generated,_name(alias)).ratio() for alias in entry['aliases']),default=0)
        city=1.0 if _city(place.get('city'))==_city(entry['city']) else 0.0
        category=str(place.get('category') or '')
        category_match=1.0 if any(value in category for value in entry['categories']) else (.5 if not category else 0.0)
        return round(.70*name+.20*city+.10*category_match,4)

    def resolve(self,place: dict,job_id: str|None=None) -> OfficialSourceResolution:
        ranked=sorted(((self._score(place,entry),entry) for entry in self.registry),key=lambda item:item[0],reverse=True)
        score,entry=ranked[0] if ranked else (0,None)
        if entry and score>=self.AUTO_BIND_THRESHOLD:
            result=OfficialSourceResolution(status='resolved',official_url=entry['official_url'],
                source_type='official_website',confidence=score,resolution_method='curated_registry_name_city_category',
                pages=entry['pages'],allowed_domains=entry['domains'])
        else:
            provider_url=(place.get('source_metadata') or {}).get('amap_website')
            host=urlparse(str(provider_url or '')).hostname
            # A provider URL is only a candidate. Without a reviewed domain and page identity
            # match it is deliberately kept below the automatic binding threshold.
            if provider_url and host:
                result=OfficialSourceResolution(status='low_confidence',official_url=None,
                    source_type='provider_candidate',confidence=.55,
                    resolution_method='amap_website_requires_identity_review')
            else:
                result=OfficialSourceResolution(status='unresolved',confidence=score,
                    resolution_method='no_trusted_official_candidate')
        if self.connect:
            with self.connect() as db:
                db.execute("""INSERT INTO official_source_resolutions
                  (resolution_id,job_id,place_id,status,official_url,source_type,confidence,resolution_method,
                   candidates_json,resolved_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (str(uuid.uuid4()),job_id,place.get('id'),result.status,result.official_url,result.source_type,
                   result.confidence,result.resolution_method,
                   json.dumps([{'url':item[1]['official_url'],'score':item[0]} for item in ranked[:3]],ensure_ascii=False),_now()))
        return result
