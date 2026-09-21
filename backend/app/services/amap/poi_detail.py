"""AMap POI ID lookup; kept separate so search is not forced to call detail."""
from __future__ import annotations

from .client import AmapClient
from .normalizer import normalize_poi
from .poi_search import AmapPoiCandidate


async def get_poi_detail(client: AmapClient,provider_place_id: str) -> tuple[AmapPoiCandidate|None,bool]:
    data,cached=await client.get('/place/detail',{'id':provider_place_id,'show_fields':'business'},
                                 operation='poi_detail_v2',base_url=client.poi_base_url)
    pois=data.get('pois') or []
    return (AmapPoiCandidate.model_validate(normalize_poi(pois[0])) if pois else None),cached
