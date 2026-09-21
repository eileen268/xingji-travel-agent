from __future__ import annotations
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

class OfficialSourceCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    url: str
    title: str|None=None
    snippet: str|None=None
    domain: str
    rank: int=Field(ge=1)
    provider: Literal['serper','google_places','manual','cache']
    query: str

class OfficialSourceDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    selected_url: str|None=None
    status: Literal['verified_official','probable_official','unverified','third_party','not_eligible','cache_hit']
    confidence: float=Field(ge=0,le=1)
    selected_candidate: OfficialSourceCandidate|None=None
    rejected_candidates: list[dict]=Field(default_factory=list,max_length=20)
    evidence: dict=Field(default_factory=dict)
