import copy
import json

import pytest

from app.models import BuildInput
from app.offline import make_packs
from app.pipeline import _offline_plan
from app.place_allocator import (
    AllocationError, allocate_trip, assign_place, build_canonical_place_pool,
    build_day_eligible_pool, finalize_selected, rebuild_available_pool,
    reallocate_days, release_day_assignments, soft_reserve_place,
    validate_cross_day_ownership,
)

PREFS=dict(origin='上海',destinations=['杭州'],mainDestination='杭州',startDate='2026-10-02',endDate='2026-10-06',
    adults=2,children=0,seniors=0,travelStyle='朋友出游',budget={'total':8000},
    preferences={'pace':'balanced','interests':['人文古迹','摄影','自然风景']})


def fixture():
    trip=BuildInput(**PREFS); packs=make_packs(trip); places=packs['places-core']['places']
    return trip,packs,places,_offline_plan(trip,packs)


def test_canonical_pool_uses_unique_ids_for_sights_and_experiences_only():
    _,_,places,_=fixture(); canonical=build_canonical_place_pool(places)
    assert len(canonical)==len(set(canonical))
    assert all(place['type'] in ('sight','experience') for place in canonical.values())
    with pytest.raises(AllocationError) as caught:
        build_canonical_place_pool([places[0],copy.deepcopy(places[0])])
    assert caught.value.errors[0]['code']=='DUPLICATE_CANONICAL_PLACE_ID'


@pytest.mark.parametrize('place_type',['sight','experience'])
def test_hard_owned_place_cannot_be_assigned_to_two_days(place_type):
    _,_,places,_=fixture(); place_id=next(p['id'] for p in places if p['type']==place_type)
    state={'place_assignments':{}}
    assign_place(state,place_id,1,'primary' if place_type=='sight' else 'preferred')
    with pytest.raises(AllocationError) as caught:assign_place(state,place_id,2,'preferred')
    assert caught.value.errors[0]['code']=='PLACE_ALREADY_OWNED'


def test_eligibility_filters_city_and_other_day_hard_ownership():
    _,_,places,plan=fixture(); canonical=build_canonical_place_pool(places)
    owned_id=next(iter(canonical)); state={'place_assignments':{}}
    assign_place(state,owned_id,1,'preferred')
    day={**plan['days'][1],'day':2}; available=rebuild_available_pool(canonical,state['place_assignments'])
    eligible=build_day_eligible_pool(day,canonical,available,state['place_assignments'])
    assert owned_id not in eligible
    assert eligible and all(canonical[pid]['city']==day['city'] for pid in eligible)


def test_preferred_is_hard_owned_while_backup_is_soft_and_releasable():
    _,_,places,_=fixture(); canonical=build_canonical_place_pool(places); ids=list(canonical)[:2]
    state={'canonical_place_ids':list(canonical),'place_assignments':{},'released_place_ids':[]}
    assign_place(state,ids[0],1,'preferred'); soft_reserve_place(state,ids[1],1)
    available=rebuild_available_pool(canonical,state['place_assignments'])
    assert ids[0] not in available and ids[1] in available
    released=release_day_assignments(state,1,canonical)
    assert set(released)==set(ids) and set(ids)<=set(state['available_place_ids'])


def test_release_day_does_not_touch_other_day_or_fixed_assignment():
    _,_,places,_=fixture(); canonical=build_canonical_place_pool(places); ids=list(canonical)[:3]
    state={'canonical_place_ids':list(canonical),'place_assignments':{},'released_place_ids':[]}
    assign_place(state,ids[0],1,'primary',fixed=True); assign_place(state,ids[1],1,'preferred'); assign_place(state,ids[2],2,'preferred')
    released=release_day_assignments(state,1,canonical)
    assert released==[ids[1]] and ids[0] in state['place_assignments'] and ids[2] in state['place_assignments']


def test_must_visit_is_hard_owned_before_general_candidate_allocation():
    _,_,places,plan=fixture(); place=next(p for p in places if p['type']=='experience')
    for day in plan['days']:
        day['candidate_place_ids']=[pid for pid in day['candidate_place_ids'] if pid!=place['id']]
        day['backup_candidate_ids']=[pid for pid in day['backup_candidate_ids'] if pid!=place['id']]
    plan['days'][2]['backup_candidate_ids']=[place['id'],*plan['days'][2]['backup_candidate_ids'][:3]]
    allocation=allocate_trip(plan,places,must_go=place['display_name'])
    owner=allocation['place_assignments'][place['id']]
    assert owner=={'place_id':place['id'],'day':3,'role':'preferred','reservation':'hard','status':'reserved','fixed':True}
    restored=json.loads(json.dumps(allocation)); released=release_day_assignments(restored,3,build_canonical_place_pool(places))
    assert place['id'] not in released and restored['place_assignments'][place['id']]['fixed'] is True


def test_global_allocation_is_unique_and_rebuilds_after_serialized_restart():
    trip,_,places,plan=fixture(); allocation=allocate_trip(plan,places)
    assert not validate_cross_day_ownership(allocation,places)
    seen=[]
    for day in allocation['days']:
        seen.extend([day['primary_anchor_id'],*day['preferred_candidate_ids'],*day['backup_candidate_ids']])
    assert len(seen)==len(set(seen))
    before_other={pid:owner for pid,owner in allocation['place_assignments'].items() if owner['day']!=3}
    restored=json.loads(json.dumps(allocation))
    rebuilt=reallocate_days(restored,plan,places,{3})
    assert {pid:owner for pid,owner in rebuilt['place_assignments'].items() if owner['day']!=3}==before_other
    canonical=build_canonical_place_pool(places)
    assert set(rebuilt['available_place_ids'])==rebuild_available_pool(canonical,rebuilt['place_assignments'])
    assert not validate_cross_day_ownership(rebuilt,places)


def test_selected_place_must_belong_to_day_and_non_owned_type_may_repeat():
    _,packs,places,plan=fixture(); allocation=allocate_trip(plan,places)
    day1=allocation['days'][0]; foreign=allocation['days'][1]['primary_anchor_id']
    itinerary=copy.deepcopy(packs['itinerary']['itinerary'])
    itinerary[0]['stops'].append({**itinerary[0]['stops'][0],'place_id':foreign})
    with pytest.raises(AllocationError) as caught:finalize_selected(allocation,itinerary,places)
    assert caught.value.errors[0]['code']=='UNOWNED_SELECTED_PLACE'
    hotel={'id':'hotel-1','type':'hotel','city':'杭州'}
    repeated=[{'stops':[{'place_id':'hotel-1'}]},{'stops':[{'place_id':'hotel-1'}]}]
    assert not validate_cross_day_ownership(allocation,[*places,hotel],repeated)
