from urllib.parse import urlparse
from ...google_places import GooglePlaceResolver
from ..models import OfficialSourceCandidate

class GooglePlacesOfficialSourceProvider:
    """Optional fallback adapter; never enabled unless explicitly configured."""
    name='google_places'
    def __init__(self,resolver: GooglePlaceResolver):self.resolver=resolver
    async def search_official_source(self,place: dict,query: str) -> list[OfficialSourceCandidate]:
        result=await self.resolver.resolve(place);selected=result.selected
        if not selected or not selected.website_uri:return []
        host=(urlparse(selected.website_uri).hostname or '').lower().removeprefix('www.')
        return [OfficialSourceCandidate(url=selected.website_uri,title=selected.display_name,
            snippet=selected.formatted_address,domain=host,rank=1,provider='google_places',query=query)]
