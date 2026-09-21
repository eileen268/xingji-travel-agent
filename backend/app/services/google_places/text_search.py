from .client import GooglePlacesClient
from .normalizer import normalize_place


async def text_search(client: GooglePlacesClient,query: str,limit: int=5):
    data,cached=await client.request('POST','/places:searchText',operation='text_search',
        field_mask=client.SEARCH_MASK,body={'textQuery':query,'languageCode':'zh-CN','regionCode':'CN','pageSize':max(1,min(limit,20))})
    return [normalize_place(item) for item in (data.get('places') or []) if item.get('id') and (item.get('displayName') or {}).get('text')],cached
