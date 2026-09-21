import copy

from app.interests import (
    derive_day_interests, enrich_place, enrich_places, evaluate_trip_preferences,
    normalize_interests, place_interest_score,
)
from app.itinerary_plan import decision_schema
from app.models import BuildInput
from app.offline import make_packs
from app.llm_chunks import day_city
from app.itinerary_plan import build_day_pools
from app.place_allocator import build_canonical_place_pool, build_day_eligible_pool
from tests.test_build import PREFS


def poi(name,description='这是用于测试语义归一化的真实地点说明，包含足够长度并提供明确的地点类型信息。',kind='sight'):
    return {'id':name,'type':kind,'city':'张掖','display_name':name,'local_name':name,'description':description,
        'cuisine':'','signature_dishes':'','experience_type':'','semantic_tags':[],'semantic_source':[]}


def test_ui_interest_labels_map_to_stable_internal_ids():
    assert normalize_interests(['自然风景','摄影','拍照与氛围','人文古迹','历史文化']) == [
        'natural_scenery','photo_atmosphere','history_culture']


def test_semantic_rules_identify_danxia_desert_old_town_and_museum():
    danxia=enrich_place(poi('张掖七彩丹霞'))
    desert=enrich_place(poi('鸣沙山沙漠景区'))
    old=enrich_place(poi('古城历史街区'))
    museum=enrich_place(poi('地方历史博物馆'))
    assert {'geopark','natural_scenic','landscape_photography'}<=set(danxia['semantic_tags'])
    assert 'desert_landform' in desert['semantic_tags']
    assert {'old_town','historic_site','historic_architecture'}<=set(old['semantic_tags'])
    assert {'museum','heritage'}<=set(museum['semantic_tags'])


def test_affinity_is_weighted_explainable_and_clamped():
    danxia=enrich_place(poi('张掖七彩丹霞'));park=enrich_place(poi('普通城市公园'));museum=enrich_place(poi('历史博物馆'))
    assert danxia['interest_affinity']['natural_scenery']>=.9
    assert danxia['interest_affinity']['photo_atmosphere']>=.9
    assert park['interest_affinity']['natural_scenery']<danxia['interest_affinity']['natural_scenery']
    assert museum['interest_affinity']['history_culture']>=.9
    assert danxia['affinity_reasons']['photo_atmosphere']
    assert all(0<=value<=1 for value in danxia['interest_affinity'].values())
    assert enrich_place(copy.deepcopy(danxia))==danxia


def test_itinerary_plan_schema_does_not_accept_llm_covered_interests():
    trip=BuildInput(**PREFS);places=enrich_places(make_packs(trip)['places-core']['places']);pools=build_day_pools(trip,places,day_city)
    props=decision_schema(pools,trip.preferences.interests)['$defs']['PlanDecisionDay']['properties']
    assert 'covered_interests' not in props


def test_candidate_ranking_uses_affinity_before_planning():
    natural=enrich_place(poi('张掖七彩丹霞'));urban=enrich_place(poi('普通城市公园'))
    canonical=build_canonical_place_pool([natural,urban]);day={'day':1,'city':'张掖','area_labels':[],'intent':''}
    ranked=build_day_eligible_pool(day,canonical,set(canonical),{},candidate_ids=[urban['id'],natural['id']],interests=['自然风景'])
    # Day text is neutral; explicit affinity must still lift the stronger POI.
    day['covered_interests']=[]
    assert place_interest_score(natural,['natural_scenery'])>place_interest_score(urban,['natural_scenery'])
    assert ranked[0]==natural['id']


def test_day_can_have_no_derived_interests():
    restaurant=enrich_place(poi('普通餐厅',kind='restaurant'))
    day={'stops':[{'place_id':restaurant['id']}]}
    assert derive_day_interests(day,[restaurant],['自然风景'])=={}


def test_trip_preference_fit_uses_actual_stops_and_soft_warnings():
    places=enrich_places([poi('张掖七彩丹霞'),poi('历史博物馆'),poi('普通餐厅',kind='restaurant')])
    itinerary=[{'stops':[{'place_id':'张掖七彩丹霞'}]},{'stops':[{'place_id':'历史博物馆'}]}]
    evaluation=evaluate_trip_preferences(itinerary,places,['自然风景','摄影','历史文化','美食'])
    assert evaluation['interest_coverage']>=.75
    assert evaluation['interest_strength']['natural_scenery']>=.9
    assert evaluation['interest_diversity']>0
    assert 0<=evaluation['preference_fit_score']<=100
    assert itinerary[0]['derived_interests'] and itinerary[0]['day_interest_strength']
    assert any(w['code']=='INTEREST_WEAK_MATCH' for w in evaluation['warnings'])


def test_false_declared_interest_never_changes_backend_derived_result():
    place=enrich_place(poi('历史博物馆'));day={'stops':[{'place_id':place['id']}],'covered_interests':['摄影']}
    first=evaluate_trip_preferences([copy.deepcopy(day)],[place],['摄影'])
    day['covered_interests']=[]
    second=evaluate_trip_preferences([day],[place],['摄影'])
    assert first==second and first['warnings'][0]['code']=='INTEREST_WEAK_MATCH'


def test_many_interests_short_trip_is_warning_not_error():
    place=enrich_place(poi('历史博物馆'));itinerary=[{'stops':[{'place_id':place['id']}]}]
    result=evaluate_trip_preferences(itinerary,[place],['自然风景','摄影','历史文化','美食','城市漫游'])
    assert any(item['code']=='INTEREST_DENSITY_HIGH' for item in result['warnings'])


def test_lanzhou_zhangye_dunhuang_interest_regression_finishes_as_soft_evaluation():
    places=enrich_places([
        {**poi('黄河滨水步道'),'id':'lz-river','city':'兰州'},
        {**poi('张掖七彩丹霞'),'id':'zy-danxia','city':'张掖'},
        {**poi('敦煌莫高窟'),'id':'dh-caves','city':'敦煌'},
    ])
    itinerary=[{'stops':[{'place_id':'lz-river'}]},{'stops':[{'place_id':'zy-danxia'}]},{'stops':[{'place_id':'dh-caves'}]}]
    result=evaluate_trip_preferences(itinerary,places,['美食','自然风景','历史文化','拍照与氛围'])
    assert result['preference_fit_score']>=0
    assert all(item['code']!='UNSUPPORTED_INTEREST' for item in result['warnings'])
