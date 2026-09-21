import asyncio

from app.models import BuildInput
from app.scheduler import schedule_day_with_routes
from app.trip_constraints import compile_trip_constraints,city_day_counts,city_for_day


BASE=dict(origin='成都',destinations=['上海','苏州'],mainDestination='上海',
          startDate='2026-09-30',endDate='2026-10-05',adults=2,children=0,seniors=0,
          travelStyle='情侣或夫妻',budget={'total':6000},
          preferences={'pace':'balanced','interests':['美食'],'localTransport':['transit']})


def trip(notes):return BuildInput(**(BASE|{'notes':notes}))


def test_city_stay_constraint_overrides_primary_destination_default():
    value=trip('苏州玩2天');constraints=compile_trip_constraints(value)
    assert city_day_counts(value,constraints)=={'上海':4,'苏州':2}
    assert [city_for_day(value,index,constraints) for index in range(value.days)]==['上海']*4+['苏州']*2


def test_explicit_primary_city_count_is_not_overwritten_by_default():
    value=trip('上海玩2天');constraints=compile_trip_constraints(value)
    assert city_day_counts(value,constraints)=={'上海':2,'苏州':4}


def test_arrival_constraint_moves_first_formal_stop_after_arrival():
    value=trip('30号下午三点才到上海');constraints=compile_trip_constraints(value)
    assert constraints.arrival_constraints[0].arrival_time=='15:00'
    places=[{'id':'anchor','type':'sight','place_type':'sight','city':'上海','display_name':'测试景点',
             'latitude':31.2,'longitude':121.4,'interest_affinity':{},'semantic_tags':[],
             'official_facts':{},'source_metadata':{}}]
    outline={'day':1,'date':'2026-09-30','city':'上海','area_labels':[],'intent':'抵达日',
             'primary_anchor_id':'anchor','candidate_place_ids':[],'backup_candidate_ids':[],
             'fixed_meal_stop_id':None}
    decision={'optional_stop_order':[],'day_intent':'抵达日','practical_notes':['抵达后开始活动。']}
    day=asyncio.run(schedule_day_with_routes(decision,outline,places,value,0,trip_constraints=constraints))
    assert day['stops'][0]['arrival_time']>='15:00'
    assert day['density_evaluation']['reduced_window'] is True
    assert day['density_evaluation']['available_minutes']<660


def test_city_stay_and_arrival_constraints_are_both_compiled():
    value=trip('苏州玩2天，30号下午3点才到上海');constraints=compile_trip_constraints(value)
    assert city_day_counts(value,constraints)=={'上海':4,'苏州':2}
    assert constraints.arrival_constraints[0].arrival_time=='15:00'


def test_departure_constraint_is_compiled():
    value=trip('10月5日上午十点离开苏州');constraints=compile_trip_constraints(value)
    item=constraints.departure_constraints[0]
    assert str(item.date)=='2026-10-05' and item.city=='苏州' and item.departure_time=='10:00'
