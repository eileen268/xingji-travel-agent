from __future__ import annotations

import math,re
from difflib import SequenceMatcher
from .client import GooglePlacesClient
from .models import GooglePlaceCandidate,GooglePlaceResolution
from .place_details import place_details
from .text_search import text_search


WEIGHTS={'name':.35,'city':.20,'geo':.20,'address':.15,'category':.10}
TYPE_HINTS={
    '博物馆':{'museum'},'公园':{'park','national_park'},'寺':{'place_of_worship','hindu_temple','buddhist_temple'},
    '乐园':{'amusement_park','theme_park'},'动物':{'zoo'},'景区':{'tourist_attraction'},
    '风景名胜':{'tourist_attraction','park','national_park'},
}


def _text(value):return re.sub(r'[\s·•（）()\-—_]+','',str(value or '')).casefold()
def _ngrams(value):
    value=_text(value)
    return {value[i:i+2] for i in range(max(1,len(value)-1))} if value else set()
def _similarity(a,b):return SequenceMatcher(None,_text(a),_text(b)).ratio()
def _address_similarity(a,b):
    left,right=_ngrams(a),_ngrams(b)
    return len(left&right)/len(left|right) if left and right else 0.0
def _distance_km(a_lat,a_lng,b_lat,b_lng):
    if None in (a_lat,a_lng,b_lat,b_lng):return None
    p1,p2=math.radians(a_lat),math.radians(b_lat);dp=math.radians(b_lat-a_lat);dl=math.radians(b_lng-a_lng)
    h=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 6371*2*math.atan2(math.sqrt(h),math.sqrt(1-h))
def _geo_score(distance):
    if distance is None:return .5
    if distance<=.1:return 1.0
    if distance<=.5:return .9
    if distance<=2:return .7
    if distance<=10:return .3
    return 0.0
def _category_score(category,types):
    expected={hint for label,hints in TYPE_HINTS.items() if label in str(category or '') for hint in hints}
    if not expected:return .5
    return 1.0 if expected&set(types) else 0.0


class GooglePlaceResolver:
    EXACT_THRESHOLD=.85;PROBABLE_THRESHOLD=.70;AMBIGUOUS_MARGIN=.08
    def __init__(self,client: GooglePlacesClient,weights=None):
        self.client=client;self.weights={**WEIGHTS,**(weights or {})}

    def score(self,place: dict,candidate: GooglePlaceCandidate):
        distance=_distance_km(place.get('latitude'),place.get('longitude'),candidate.latitude,candidate.longitude)
        breakdown={
            'name':max(_similarity(place.get('local_name'),candidate.display_name),
                       _similarity(place.get('canonical_name'),candidate.display_name)),
            'city':1.0 if _text(place.get('city')) in _text(candidate.formatted_address) else 0.0,
            'geo':_geo_score(distance),'address':_address_similarity(place.get('address'),candidate.formatted_address),
            'category':_category_score(place.get('category'),candidate.types),
        }
        total=sum(self.weights[key]*value for key,value in breakdown.items())
        return round(total,4),{key:round(value,4) for key,value in breakdown.items()},None if distance is None else round(distance,3)

    async def resolve(self,place: dict) -> GooglePlaceResolution:
        query=f"{place.get('local_name') or place.get('canonical_name')} {place.get('city')}".strip()
        if place.get('place_type')=='generated_experience' and not place.get('linked_place_id'):
            return GooglePlaceResolution(status='skipped_generated_experience',score=0,query=query,
                                         reason='generated experience without linked real place')
        candidates,_=await text_search(self.client,query,5)
        ranked=[]
        for candidate in candidates:
            score,breakdown,distance=self.score(place,candidate)
            ranked.append((score,candidate,breakdown,distance))
        ranked.sort(key=lambda item:item[0],reverse=True)
        debug=[{'google_place_id':item[1].google_place_id,'display_name':item[1].display_name,
                'formatted_address':item[1].formatted_address,'score':item[0],'score_breakdown':item[2],
                'distance_km':item[3]} for item in ranked[:5]]
        if not ranked:
            return GooglePlaceResolution(status='no_match',score=0,candidates=[],query=query,reason='no search candidates')
        top=ranked[0];margin=top[0]-(ranked[1][0] if len(ranked)>1 else 0)
        if top[0]>=self.PROBABLE_THRESHOLD and len(ranked)>1 and margin<self.AMBIGUOUS_MARGIN:
            return GooglePlaceResolution(status='ambiguous',score=top[0],candidates=debug,query=query,
                                         reason=f'top candidate margin {margin:.3f} below {self.AMBIGUOUS_MARGIN}')
        if top[0]<self.PROBABLE_THRESHOLD:
            return GooglePlaceResolution(status='no_match',score=top[0],candidates=debug,query=query,
                                         reason='top candidate below probable threshold')
        status='exact_match' if top[0]>=self.EXACT_THRESHOLD else 'probable_match'
        detailed,_=await place_details(self.client,top[1].google_place_id)
        return GooglePlaceResolution(status=status,selected=detailed,score=top[0],candidates=debug,query=query,
                                     reason='selected highest candidate after multi-signal scoring')
