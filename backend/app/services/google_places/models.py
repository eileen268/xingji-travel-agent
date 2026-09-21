from __future__ import annotations

from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class GooglePlaceCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    google_place_id: str
    display_name: str
    formatted_address: str|None=None
    latitude: float|None=Field(default=None,ge=-90,le=90)
    longitude: float|None=Field(default=None,ge=-180,le=180)
    types: list[str]=Field(default_factory=list)
    website_uri: str|None=None
    regular_opening_hours: dict|None=None
    national_phone_number: str|None=None
    international_phone_number: str|None=None
    business_status: str|None=None


class GooglePlaceResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: Literal['exact_match','probable_match','ambiguous','no_match','skipped_generated_experience']
    selected: GooglePlaceCandidate|None=None
    score: float=Field(ge=0,le=1)
    candidates: list[dict]=Field(default_factory=list,max_length=5)
    query: str
    reason: str
