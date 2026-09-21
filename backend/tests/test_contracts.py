import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.contracts import (PACK_CONTRACT_VERSIONS, adapt_pack_payload, collect_places, contract_version,
                           language_request_models, practical_request_models,
                           research_batch_model, split_place_packs, validate_pack_model)
from app.food_contract import FOOD_MAX_PER_CITY, FOOD_MIN_PER_CITY, food_batch_model
from app.models import BuildInput, DayDecision, DestinationProfile, pack_model
from app.pipeline import assemble
from app.validators import check_handoff

FIXTURES=Path(__file__).parent/'fixtures/contracts'

def load(name):
    return json.loads((FIXTURES/name).read_text(encoding='utf-8'))

def fixture_pack_ids():
    return {
        'framing.valid.json':'framing','places-core.valid.json':'places-core',
        'places-experiences.valid.json':'places-experiences','places-food.valid.json':'places-food',
        'itinerary-plan.valid.json':'itinerary-plan','itinerary-allocation.valid.json':'itinerary-allocation',
        'itinerary-day.valid.json':'itinerary-day-1','itinerary-coherence.valid.json':'itinerary-coherence',
        'modules-practical.valid.json':'modules-practical','modules-language-notes.valid.json':'modules-language-notes',
        'destination-profile.valid.json':'destination-profile',
    }

def test_every_persisted_pack_has_a_version_and_model():
    for filename,pack_id in fixture_pack_ids().items():
        assert contract_version(pack_id)
        assert pack_model(pack_id) is not None
        assert validate_pack_model(pack_id,load(filename))
    assert set(PACK_CONTRACT_VERSIONS)=={
        'framing','places-core','places-experiences','places-food','itinerary-plan',
        'itinerary-allocation','itinerary-day-*','itinerary-coherence','modules-practical',
        'modules-language-notes','destination-profile'}

def test_pydantic_and_runtime_json_schema_accept_same_fixtures():
    for filename,pack_id in fixture_pack_ids().items():
        payload=load(filename);model=pack_model(pack_id)
        # Some canonical fixtures intentionally exercise legacy adapters. The
        # runtime JSON schema validates the canonical output of that explicit
        # adapter, not the historical input shape.
        canonical=model.model_validate(payload).model_dump(mode='json')
        assert not list(Draft202012Validator(model.model_json_schema()).iter_errors(canonical))


def test_checked_in_schemas_are_generated_from_pydantic():
    schema_root=Path(__file__).parents[1]/'schemas'
    exported=json.loads((schema_root/'research-pack-contract.seven.json').read_text(encoding='utf-8'))
    for filename,pack_id in fixture_pack_ids().items():
        key='itinerary-day-*' if pack_id.startswith('itinerary-day-') else pack_id
        assert exported['packs'][key]==pack_model(pack_id).model_json_schema()
    destination=json.loads((schema_root/'destination-profile.seven.schema.json').read_text(encoding='utf-8'))
    destination.pop('$schema');destination.pop('$comment')
    assert destination==DestinationProfile.model_json_schema()

def test_place_pack_types_and_legacy_adapter_are_explicit():
    core=load('places-core.valid.json');experiences=load('places-experiences.valid.json');food=load('places-food.valid.json')
    assert {p['type'] for p in core['places']}=={'sight'}
    assert {p['type'] for p in experiences['places']}=={'experience'}
    assert {p['type'] for p in food['places']}<= {'restaurant','chain'}
    aggregate={'places-core':{'places':collect_places({**{'places-core':core},'places-experiences':experiences,'places-food':food})}}
    adapted=split_place_packs(aggregate)
    assert collect_places(adapted)==aggregate['places-core']['places']


def test_legacy_declared_interests_have_one_explicit_adapter():
    plan=load('itinerary-plan.valid.json');plan['days'][0]['covered_interests']=['摄影']
    adapted=adapt_pack_payload('itinerary-plan',plan)
    assert all('covered_interests' not in day for day in adapted['days'])
    assert validate_pack_model('itinerary-plan',plan)==adapted


def test_legacy_allocation_and_profile_are_deterministically_upgraded():
    allocation=load('itinerary-allocation.valid.json')
    allocation.pop('selected_interest_ids');allocation.pop('trip_interest_state')
    upgraded=validate_pack_model('itinerary-allocation',allocation)
    assert upgraded['selected_interest_ids']==[] and upgraded['trip_interest_state']=={}
    profile=load('destination-profile.valid.json')
    profile.pop('preference_evaluation');profile.pop('quality_evaluation')
    upgraded_profile=validate_pack_model('destination-profile',profile)
    assert 'preference_fit_score' in upgraded_profile['preference_evaluation']
    assert 'food' in upgraded_profile['quality_evaluation']

def test_prompt_request_models_share_budget_configuration():
    for days in (2,5):
        budget,menu,snacks,preparation=practical_request_models(days)
        assert menu.model_json_schema()['properties']['cards']['minItems']==budget['menu_cards']
        assert snacks.model_json_schema()['properties']['local_snacks']['maxItems']==budget['snacks']
        prep=preparation.model_json_schema()['properties']
        assert prep['essentials']['minItems']+prep['confirm_ahead']['minItems']==budget['checklist']
        group,note=language_request_models(days)
        assert group.model_json_schema()['properties']['items']['minItems']==budget['language_items']
        assert note.model_json_schema()['properties']['items']['maxItems']==budget['travel_note_items']
    sight=research_batch_model('杭州','sight',4).model_json_schema()['properties']['places']
    assert sight['minItems']==sight['maxItems']==4
    food=food_batch_model('杭州').model_json_schema()['properties']['places']
    assert (food['minItems'],food['maxItems'])==(FOOD_MIN_PER_CITY,FOOD_MAX_PER_CITY)

def test_deprecated_interest_and_llm_hard_fields_are_absent():
    plan=pack_model('itinerary-plan').model_json_schema()['$defs']['PlanDay']['properties']
    assert 'covered_interests' not in plan
    assert set(DayDecision.model_json_schema()['properties'])=={'optional_stop_order','day_intent','practical_notes'}
    stop=DestinationProfile.model_json_schema()['$defs']['Stop']['properties']
    assert stop['transfer_minutes']['anyOf'][-1]=={'type':'null'}
    assert stop['distance_km']['anyOf'][-1]=={'type':'null'}
    assert stop['estimated_cost']['type']=='null'
    assert {'route_status','route_provider','route_queried_at'} <= set(stop)
    place=DestinationProfile.model_json_schema()['$defs']['Place']['properties']
    assert place['official_url']['anyOf'][-1]=={'type':'null'}
    assert all(place[field]['type']=='null' for field in ('coordinates','rating','review_count'))

def test_valid_pack_chain_compiles_end_to_end_without_llm():
    framing=load('framing.valid.json');trip=BuildInput.model_validate(framing['preferences'])
    destination=load('destination-profile.valid.json')
    packs={
        'places-core':load('places-core.valid.json'),
        'places-experiences':load('places-experiences.valid.json'),
        'places-food':load('places-food.valid.json'),
        'itinerary':{'itinerary':destination['itinerary']},
        'modules-practical':load('modules-practical.valid.json'),
        'modules-language-notes':load('modules-language-notes.valid.json'),
    }
    profile=assemble(trip,'contract-e2e',packs,'offline',[])
    assert not check_handoff(profile,packs)
