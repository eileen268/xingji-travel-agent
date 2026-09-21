import pytest
from pydantic import ValidationError

from app.replan_intent import StructuredReplanRequest, normalize_replan_request, parse_replan_deterministic, resolve_scope


CATALOG=[
    {'place_id':'museum','name':'上海博物馆','arrival_time':'09:00'},
    {'place_id':'tianzifang','name':'田子坊','arrival_time':'14:00'},
    {'place_id':'bund','name':'外滩','arrival_time':'17:00'},
    {'place_id':'yuyuan','name':'豫园','arrival_time':'18:00'},
    {'place_id':'disney','name':'上海迪士尼乐园','arrival_time':'09:00'},
]


@pytest.mark.parametrize(('instruction','scope','types'),[
    ('晚上加东方明珠','current_day',['add_place']),
    ('下午3点去武康大楼','current_day',['add_place']),
    ('删除田子坊','current_day',['remove_place']),
    ('其余行程删除','current_day',['remove_place']),
    ('保留上海博物馆，其余删掉','current_day',['keep_place','remove_place']),
    ('把田子坊换成武康大楼','current_day',['replace_place']),
    ('把博物馆放上午','current_day',['move_place']),
    ('把上海博物馆挪到第三天','affected_days',['move_place']),
    ('迪士尼安排一天','current_day',['dedicate_day']),
    ('迪士尼一天，其余删除','current_day',['dedicate_day','remove_place']),
    ('迪士尼一天，其余放别天','affected_days',['dedicate_day']),
    ('今天尽量打车','current_day',['set_transport']),
    ('外滩到豫园打车','current_day',['set_transport']),
    ('今天轻松一点','current_day',['set_pace']),
    ('晚上留2小时自由活动','current_day',['add_free_time']),
    ('午饭吃本帮菜','current_day',['set_meal_preference']),
    ('上午室内，下午外滩','current_day',['move_place','set_activity_preference']),
    ('不要博物馆','current_day',['avoid']),
    ('外滩一定要去','current_day',['must_include']),
    ('第三天改去苏州','affected_days',['set_city_day']),
    ('苏州多安排一天','affected_days',['set_city_day']),
    ('重新安排整个上海段','affected_days',['reflow']),
    ('整趟行程少走路','whole_trip',['set_pace']),
    ('所有天都尽量坐地铁','whole_trip',['set_transport']),
    ('今天重新排一下，加东方明珠','current_day',['replan_day','add_place']),
    ('保留上海博物馆，删田子坊，下午加武康大楼，尽量打车','current_day',
     ['keep_place','remove_place','add_place','set_transport']),
])
def test_intent_coverage_matrix(instruction,scope,types):
    request=resolve_scope(instruction,parse_replan_deterministic(instruction,CATALOG,2,6),2,6)
    actual=[action.type for action in request.actions]
    assert request.scope==scope
    for action_type in types:assert action_type in actual


def test_relative_selected_stop_lock_and_afternoon_group_move():
    locked=parse_replan_deterministic('这个景点锁住',CATALOG,2,6,'museum')
    assert any(action.type=='keep_place' and action.place_ref=='museum' and action.lock for action in locked.actions)
    moved=parse_replan_deterministic('把今天下午的两个地方移到第四天',CATALOG,2,6)
    assert moved.scope=='affected_days'
    assert any(action.type=='move_place' and action.selector=='current_day_afternoon' and action.target_day_id==4 for action in moved.actions)


def test_legacy_contract_is_normalized_once_and_null_lists_never_escape():
    normalized=normalize_replan_request({'keep':['上海博物馆'],'remove':None,'must_include_queries':None,
        'must_exclude_queries':None,'transportation_preference':'打车','time_constraints':None,'budget_constraints':None},CATALOG)
    parsed=StructuredReplanRequest.model_validate(normalized)
    assert [action.type for action in parsed.actions]==['keep_place','set_transport']
    assert parsed.affected_day_ids==[]


def test_canonical_contract_forbids_unknown_parser_fields():
    with pytest.raises((ValueError,ValidationError)):
        StructuredReplanRequest.model_validate(normalize_replan_request({'scope':'current_day','actions':[],'mystery':1}))


def test_dedicated_day_delete_and_redistribute_are_distinct():
    deleted=parse_replan_deterministic('迪士尼一天，其余删除',CATALOG,2,6)
    redistributed=parse_replan_deterministic('迪士尼一天，其余放别天',CATALOG,2,6)
    assert next(action for action in deleted.actions if action.type=='dedicate_day').redistribute_removed_stops is False
    assert next(action for action in redistributed.actions if action.type=='dedicate_day').redistribute_removed_stops is True


def test_add_place_exact_and_approximate_time_contract():
    exact=parse_replan_deterministic('晚上7点加东方明珠，其他安排不要动',CATALOG,3,6)
    action=next(value for value in exact.actions if value.type=='add_place')
    assert action.query=='东方明珠' and action.requested_start_time=='19:00'
    assert action.time_constraint_strength=='hard' and action.time_tolerance_minutes==0 and action.preserve_existing_stops
    approximate=parse_replan_deterministic('大概晚上7点加东方明珠',CATALOG,3,6)
    action=next(value for value in approximate.actions if value.type=='add_place')
    assert action.time_constraint_strength=='strong' and action.time_tolerance_minutes==30
