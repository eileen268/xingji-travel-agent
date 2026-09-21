from urllib.parse import quote
from .client import GooglePlacesClient
from .normalizer import normalize_place


async def place_details(client: GooglePlacesClient,place_id: str):
    data,cached=await client.request('GET',f'/places/{quote(place_id,safe="")}',operation='place_details',
        field_mask=client.DETAILS_MASK,params={'languageCode':'zh-CN','regionCode':'CN'})
    return normalize_place(data),cached
