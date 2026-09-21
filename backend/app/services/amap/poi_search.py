"""AMap keyword search mapped to a provider-neutral candidate."""
from __future__ import annotations

from pydantic import BaseModel,ConfigDict,Field

from .client import AmapClient
from .normalizer import normalize_poi


class AmapPoiCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider: str='amap'
    provider_place_id: str
    name: str
    city: str
    district: str|None=None
    address: str|None=None
    latitude: float|None=Field(default=None,ge=-90,le=90)
    longitude: float|None=Field(default=None,ge=-180,le=180)
    category: str=''
    subcategory: str|None=None
    typecode: str|None=None
    adcode: str|None=None
    citycode: str|None=None
    website: str|None=None
    phone: str|None=None
    opening_hours: str|None=None
    opening_hours_today: str|None=None
    opening_hours_weekly: str|None=None
    provider_rating: float|None=None
    provider_cost: float|None=None
    business_area: str|None=None
    business_tags: list[str]=Field(default_factory=list)
    aliases: list[str]=Field(default_factory=list)
    business: dict=Field(default_factory=dict)


async def search_pois(client: AmapClient,keyword: str,city: str,limit: int=10) -> tuple[list[AmapPoiCandidate],bool]:
    data,cached=await client.get('/place/text',{
        'keywords':keyword,'region':city,'city_limit':'true','show_fields':'business',
        'page_size':max(1,min(limit,25)),'page_num':1,
    },operation='poi_search_v2',base_url=client.poi_base_url)
    candidates=[]
    for raw in data.get('pois') or []:
        normalized=normalize_poi(raw)
        if normalized.get('provider_place_id') and normalized.get('name'):
            candidates.append(AmapPoiCandidate.model_validate(normalized))
    return candidates,cached
