"""Deterministic replay of saved packs. This module never creates an LLM client."""
from __future__ import annotations

from .day_planner import canonicalize_day_decision
from .scheduler import schedule_day_with_routes
from .itinerary_plan import build_day_pools, normalize_day_candidate_pools, validate_decision
from .llm_chunks import day_city
from .pack_graph import dependencies
from .place_allocator import allocate_trip, allocation_outline, finalize_selected, validate_cross_day_ownership
from .pipeline import assemble
from .validators import check_handoff, validate_day, validate_trip_coherence
from .interests import enrich_places
from .contracts import split_place_packs
from .models import StructuredTripConstraints
from .trip_constraints import compile_trip_constraints


REPLAY_POINTS=("itinerary-plan","itinerary-day","itinerary-coherence","destination-profile")


def _payload(store, pack_id):
    value=store.get_source_payload(pack_id)
    if value is None: raise ValueError(f"replay source missing pack: {pack_id}")
    return value


async def replay_saved(t, job_id, source_store, target_store, from_pack: str, manifest: dict, progress):
    if from_pack not in REPLAY_POINTS and not from_pack.startswith("itinerary-day-"):
        raise ValueError("unsupported replay point")
    target_store.copy_from(source_store.job_id)
    target_store.copy_place_resolutions_from(source_store.job_id)
    places=[]
    for name in ("places-core","places-experiences","places-food"):
        places.extend(_payload(source_store,name)["places"])
    enrich_places(places)
    framing=_payload(source_store,'framing')
    trip_constraints=StructuredTripConstraints.model_validate(framing.get('constraints') or compile_trip_constraints(t))
    plan=normalize_day_candidate_pools(_payload(source_store,"itinerary-plan"))
    pools=build_day_pools(t,places,lambda trip,index:day_city(trip,index,trip_constraints))
    errors,warnings=validate_decision(plan,pools,places,t.preferences.interests,t.preferences.mustGo,t.preferences.avoid)
    if errors: raise ValueError(f"replay itinerary-plan invalid: {errors[:3]}")
    target_store.mark_running("itinerary-plan")
    target_store.save_valid("itinerary-plan",plan,target_store.expected_signature("itinerary-plan",dependencies("itinerary-plan",t.days)),
                            validation_warnings=warnings,observability={"repair_result":"replay"},source_payload=plan)
    progress("itinerary-allocation",35)
    allocation=allocate_trip(plan,places,must_go=t.preferences.mustGo,interests=t.preferences.interests)
    target_store.mark_running("itinerary-allocation")
    target_store.save_valid("itinerary-allocation",allocation,target_store.expected_signature("itinerary-allocation",dependencies("itinerary-allocation",t.days)),
                            observability={"repair_result":"replay"},source_payload=allocation)
    itinerary=[]
    for i,outline_day in enumerate(plan["days"],1):
        outline=allocation_outline(allocation,outline_day)
        source=_payload(source_store,f"itinerary-day-{i}")
        if "optional_stop_order" in source:
            decision=canonicalize_day_decision(source)
        else:
            fixed={outline["primary_anchor_id"],outline.get("fixed_meal_stop_id")}
            decision={"optional_stop_order":[s["place_id"] for s in source.get("stops",[]) if s["place_id"] not in fixed],
                      "day_intent":source.get("theme",outline.get("intent","当日行程")),
                      "practical_notes":[source.get("summary","按现场情况灵活调整行程。")[:3000]]}
        # Replay executes current deterministic scheduling code without provider calls.
        day=await schedule_day_with_routes(decision,outline,places,t,i-1,None,target_store.connect,job_id,
                                           trip_constraints=trip_constraints)
        errors=validate_day(day,places,expected_date=outline["date"],expected_city=outline["city"],
                            required_ids=(outline["primary_anchor_id"],outline.get("fixed_meal_stop_id")))
        if errors: raise ValueError(f"replay itinerary-day-{i} invalid: {errors[:3]}")
        pid=f"itinerary-day-{i}"; target_store.mark_running(pid)
        target_store.save_valid(pid,day,target_store.expected_signature(pid,dependencies(pid,t.days)),
                                observability={"repair_result":"replay"},source_payload=decision)
        itinerary.append(day)
    allocation=finalize_selected(allocation,itinerary,places)
    if validate_cross_day_ownership(allocation,places,itinerary): raise ValueError("replay ownership invalid")
    coherence=validate_trip_coherence(itinerary,plan,places,t.preferences.model_dump(mode="json"))
    if coherence: raise ValueError(f"replay coherence invalid: {coherence[:3]}")
    target_store.mark_running("itinerary-coherence")
    target_store.save_valid("itinerary-coherence",{"passed":True,"errors":[]},target_store.expected_signature("itinerary-coherence",dependencies("itinerary-coherence",t.days)),observability={"repair_result":"replay"})
    packs=split_place_packs({"places-core":{"places":places},"itinerary":{"itinerary":itinerary},
           "modules-practical":_payload(source_store,"modules-practical"),
           "modules-language-notes":_payload(source_store,"modules-language-notes")})
    profile=assemble(t,job_id,packs,"replay",["本档案由已保存资料包重新校验和编译，未调用模型。"],manifest,target_store)
    errors=check_handoff(profile,packs)
    if errors: raise ValueError(f"replay handoff invalid: {errors[:3]}")
    target_store.mark_running("destination-profile")
    target_store.save_valid("destination-profile",profile,target_store.expected_signature("destination-profile",dependencies("destination-profile",t.days)),observability={"repair_result":"replay"})
    progress("destination-profile",95)
    return profile,packs
