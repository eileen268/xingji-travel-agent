"""Regenerate canonical pack fixtures without calling an LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.contracts import split_place_packs, validate_pack_model
from app.models import BuildInput
from app.offline import make_packs
from app.pipeline import _offline_plan, assemble, rebuild_offline_itinerary
from app.place_allocator import allocate_trip, finalize_selected

PREFERENCES={
    'origin':'上海','destinations':['杭州'],'mainDestination':'杭州',
    'startDate':'2026-10-02','endDate':'2026-10-04','adults':2,'children':0,'seniors':0,
    'travelStyle':'朋友出行','budget':{'total':6000},
    'preferences':{'pace':'balanced','interests':['历史文化','自然风景'],'localTransport':['transit']},
}

if __name__=='__main__':
    # Keep fixture construction explicit so a schema change fails at its pack boundary.
    trip=BuildInput(**PREFERENCES)
    packs=rebuild_offline_itinerary(trip,split_place_packs(make_packs(trip)))
    from app.contracts import collect_places
    places=collect_places(packs)
    plan=_offline_plan(trip,packs)
    allocation=finalize_selected(allocate_trip(plan,places,interests=trip.preferences.interests),packs['itinerary']['itinerary'],places)
    profile=assemble(trip,'contract-fixture',packs,'offline',[])
    fixtures={
        'framing.valid.json':{'preferences':trip.model_dump(mode='json')},
        'places-core.valid.json':packs['places-core'],
        'places-experiences.valid.json':packs['places-experiences'],
        'places-food.valid.json':packs['places-food'],
        'itinerary-plan.valid.json':plan,
        'itinerary-allocation.valid.json':allocation,
        'itinerary-day.valid.json':packs['itinerary']['itinerary'][0],
        'itinerary-coherence.valid.json':{'passed':True,'errors':[]},
        'modules-practical.valid.json':packs['modules-practical'],
        'modules-language-notes.valid.json':packs['modules-language-notes'],
        'destination-profile.valid.json':profile,
    }
    target=Path(__file__).resolve().parents[1]/'tests/fixtures/contracts';target.mkdir(parents=True,exist_ok=True)
    for filename,payload in fixtures.items():
        pack_id=filename.removesuffix('.valid.json')
        validate_pack_model('itinerary-day-1' if pack_id=='itinerary-day' else pack_id,payload)
        (target/filename).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
