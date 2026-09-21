"""Canonical pack contracts and explicit adapters for historical payloads."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, create_model
from .budgets import enrichment_budget
from .models import (ChecklistItem, LanguageGroup, LanguageItem, MenuGuide, MenuTerm, Note,
                     ResearchPlace, Snack, StrictModel, TravelNote, pack_model)
from .place_names import normalize_places, normalize_profile_names

PLACE_PACK_TYPES={
    'places-core':{'sight'},
    'places-experiences':{'experience'},
    'places-food':{'restaurant','chain'},
}

PACK_CONTRACT_VERSIONS={
    'framing':'framing-v2-structured-constraints',
    'places-core':'places-core-v6-amap-business',
    'places-experiences':'places-experiences-v6-amap-business',
    'places-food':'places-food-v7-amap-business',
    'itinerary-plan':'itinerary-plan-v3',
    'itinerary-allocation':'itinerary-allocation-v2',
    'itinerary-day-*':'itinerary-day-v5-route-segments',
    'itinerary-coherence':'itinerary-coherence-v2',
    'modules-practical':'modules-practical-v3',
    'modules-language-notes':'modules-language-notes-v2',
    'destination-profile':'destination-profile-v15-route-segments',
}

# Releases before the audit wrote these stale global values even though the
# job manifest already described the newer provider-retry pipeline.
LEGACY_METADATA_VERSIONS={
    ('seven-packs-4-provider-retry','resumable-4-research-checkpoints','seven-v1'),
    ('seven-packs-7-provider-retry','resumable-7-research-checkpoints','seven-v1'),
}

def contract_version(pack_id: str) -> str:
    return PACK_CONTRACT_VERSIONS['itinerary-day-*'] if pack_id.startswith('itinerary-day-') else PACK_CONTRACT_VERSIONS[pack_id]

def split_place_packs(packs: dict) -> dict:
    """Adapt the historical aggregate places-core payload at one named boundary."""
    if all(name in packs for name in PLACE_PACK_TYPES):
        return packs
    core=packs.get('places-core',{}).get('places',[])
    if not core:
        return packs
    result=dict(packs)
    for name,types in PLACE_PACK_TYPES.items():
        result[name]={'places':[place for place in core if place.get('type') in types]}
    return result

def collect_places(packs: dict) -> list[dict]:
    normalized=split_place_packs(packs)
    return [place for name in (*PLACE_PACK_TYPES,'user-places')
            for place in normalize_places(normalized.get(name,{}).get('places',[]))]

def adapt_pack_payload(pack_id: str, payload):
    """Remove only documented, non-authoritative legacy fields before model validation."""
    if pack_id in PLACE_PACK_TYPES and isinstance(payload,dict) and isinstance(payload.get('places'),list):
        payload={**payload,'places':normalize_places(payload['places'])}
    if pack_id=='itinerary-plan' and isinstance(payload,dict):
        payload={**payload,'days':[{key:value for key,value in day.items() if key!='covered_interests'}
                                   for day in payload.get('days',[])]}
    if pack_id=='itinerary-allocation' and isinstance(payload,dict):
        payload={**payload,'days':[{key:value for key,value in day.items() if key!='covered_interests'}
                                   for day in payload.get('days',[])]}
        payload.setdefault('selected_interest_ids',[])
        payload.setdefault('trip_interest_state',{})
    if pack_id=='destination-profile' and isinstance(payload,dict):
        payload=normalize_profile_names(payload)
        if 'fact_verification' not in payload:
            from .fact_verification import verify_trip_facts
            payload['fact_verification']=verify_trip_facts(payload.get('places',[]),payload.get('itinerary',[]))
        if 'preference_evaluation' not in payload:
            from .interests import evaluate_trip_preferences
            interests=((payload.get('trip') or {}).get('preferences') or {}).get('interests',[])
            payload['preference_evaluation']=evaluate_trip_preferences(payload.get('itinerary',[]),payload.get('places',[]),interests)
        if 'quality_evaluation' not in payload:
            from .models import BuildInput
            from .quality import evaluate_profile_quality
            trip=BuildInput.model_validate(payload['trip'])
            payload['quality_evaluation']=evaluate_profile_quality(trip,payload.get('places',[]),payload.get('itinerary',[]),
                payload.get('module_groups',{}),payload['preference_evaluation'])
    return payload

def validate_pack_model(pack_id: str, payload):
    model=pack_model(pack_id)
    if model is None:
        raise KeyError(pack_id)
    return model.model_validate(adapt_pack_payload(pack_id,payload)).model_dump(mode='json')

def research_batch_model(city: str, kind: str, count: int):
    place=create_model(f'{kind.title()}ResearchPlace',__base__=ResearchPlace,
                       city=(Literal[city],...),type=(Literal[kind],...))
    return create_model(f'{kind.title()}ResearchBatch',__base__=StrictModel,
                        places=(list[place],Field(min_length=count,max_length=count)))

def practical_request_models(days: int):
    budget=enrichment_budget(days);half=budget['checklist']//2
    menu=create_model('BudgetMenuGuide',__base__=MenuGuide,
        cards=(list[Note],Field(min_length=budget['menu_cards'],max_length=budget['menu_cards'])),
        dictionary=(list[MenuTerm],Field(min_length=budget['menu_terms'],max_length=budget['menu_terms'])))
    snacks=create_model('BudgetSnacks',__base__=StrictModel,
        local_snacks=(list[Snack],Field(min_length=budget['snacks'],max_length=budget['snacks'])))
    preparation=create_model('BudgetPreparation',__base__=StrictModel,
        essentials=(list[ChecklistItem],Field(min_length=half,max_length=half)),
        confirm_ahead=(list[ChecklistItem],Field(min_length=budget['checklist']-half,max_length=budget['checklist']-half)))
    return budget,menu,snacks,preparation

def language_request_models(days: int):
    budget=enrichment_budget(days)
    group=create_model('BudgetLanguageGroup',__base__=LanguageGroup,
        items=(list[LanguageItem],Field(min_length=budget['language_items'],max_length=budget['language_items'])))
    note=create_model('BudgetTravelNote',__base__=TravelNote,
        items=(list[Note],Field(min_length=budget['travel_note_items'],max_length=budget['travel_note_items'])))
    return group,note
