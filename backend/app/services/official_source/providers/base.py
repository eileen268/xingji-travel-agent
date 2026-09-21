from typing import Protocol
from ..models import OfficialSourceCandidate

class OfficialSourceDiscoveryProvider(Protocol):
    name: str
    async def search_official_source(self,place: dict,query: str) -> list[OfficialSourceCandidate]:...
