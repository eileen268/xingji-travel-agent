import copy

from app.models import BuildInput
from app.offline import make_packs
from app.pipeline import assemble
from app.quality import evaluate_profile_quality, recommended_food_scene_target
from app.validators import validate_destination_data
from tests.test_build import PREFS


def profile_fixture(preferences=PREFS):
    trip=BuildInput(**preferences);packs=make_packs(trip)
    return trip,packs,assemble(trip,'quality-test',packs,'mock',[])


def set_food_scenes(profile,count):
    refs=profile['module_groups']['food']['dedicated_trip']
    restaurant_ids={r['place_id'] for r in refs}
    for index,place in enumerate(p for p in profile['places'] if p['id'] in restaurant_ids):
        place['cuisine']=f'餐饮场景{index%count}'


def recalculate(trip,profile):
    return evaluate_profile_quality(trip,profile['places'],profile['itinerary'],profile['module_groups'],profile['preference_evaluation'])


def test_four_food_scenes_meet_long_food_trip_quality_target():
    trip,_,profile=profile_fixture();set_food_scenes(profile,4)
    quality=recalculate(trip,profile)
    assert quality['food']['recommended_scene_target']==4
    assert quality['food']['actual_scene_count']==4
    assert not any(w['code']=='FOOD_SCENE_DIVERSITY_LOW' for w in quality['warnings'])


def test_three_food_scenes_are_valid_but_warn_for_long_food_trip():
    trip,_,profile=profile_fixture();set_food_scenes(profile,3)
    profile['quality_evaluation']=recalculate(trip,profile)
    assert not validate_destination_data(profile)
    assert any(w['code']=='FOOD_SCENE_DIVERSITY_LOW' for w in profile['quality_evaluation']['warnings'])


def test_food_quality_warning_is_added_during_profile_compilation():
    trip=BuildInput(**PREFS);packs=make_packs(trip)
    restaurant_ids={r['place_id'] for r in packs['modules-practical']['food']['dedicated_trip']}
    for index,place in enumerate(p for p in packs['places-core']['places'] if p['id'] in restaurant_ids):
        place['cuisine']=f'合理场景{index%3}'
    profile=assemble(trip,'quality-warning',packs,'mock',[])
    assert any(w['code']=='FOOD_SCENE_DIVERSITY_LOW' for w in profile['quality_evaluation']['warnings'])
    assert any('餐饮场景较集中' in warning['message'] for warning in profile['generation']['warnings'])
    assert not validate_destination_data(profile)


def test_two_food_scenes_are_enough_for_short_trip_without_food_interest():
    prefs=copy.deepcopy(PREFS);prefs['endDate']='2026-10-03';prefs['preferences']['interests']=['人文古迹']
    trip,_,profile=profile_fixture(prefs);set_food_scenes(profile,2)
    quality=recalculate(trip,profile)
    assert recommended_food_scene_target(trip.days,False)==1
    assert not any(w['code']=='FOOD_SCENE_DIVERSITY_LOW' for w in quality['warnings'])


def test_food_quality_evaluation_never_fabricates_an_extra_scene():
    trip,_,profile=profile_fixture();set_food_scenes(profile,3)
    before=copy.deepcopy((profile['places'],profile['module_groups']['food']))
    quality=recalculate(trip,profile)
    assert (profile['places'],profile['module_groups']['food'])==before
    assert quality['food']['actual_scene_count']==3


def test_profile_richness_checks_are_warnings_not_hard_failures():
    trip,_,profile=profile_fixture();by_id={p['id']:p for p in profile['places']}
    for group in profile['module_groups']['experiences']:
        for ref in group['items']:by_id[ref['place_id']]['experience_type']='culture'
    profile['module_groups']['travel_notes'][0]['items'][0]['note']='这是一条长度达到基础结构要求但内容仍然稍显简略的现场行动提示说明。'
    group=profile['module_groups']['language']['keyword_groups'][0]
    group['items'][1]['text']=group['items'][0]['text']
    profile['quality_evaluation']=recalculate(trip,profile)
    assert not validate_destination_data(profile)
    codes={w['code'] for w in profile['quality_evaluation']['warnings']}
    assert {'EXPERIENCE_DIVERSITY_LOW','LANGUAGE_DIVERSITY_LOW'}<=codes


def test_multicity_food_city_coverage_is_a_soft_metric():
    prefs=copy.deepcopy(PREFS);prefs.update(destinations=['兰州','张掖','敦煌'],mainDestination='兰州',endDate='2026-10-04')
    trip=BuildInput(**prefs)
    places=[];itinerary=[];dedicated=[]
    for index,city in enumerate(trip.destinations):
        pid=f'r-{index}';places.append({'id':pid,'type':'restaurant','city':city,'cuisine':f'{city}地方餐饮',
            'interest_affinity':{'food':.9}})
        itinerary.append({'stops':[{'place_id':pid}]});dedicated.append({'place_id':pid})
    modules={'food':{'dedicated_trip':dedicated},'experiences':[],'travel_notes':[],
             'language':{'keyword_groups':[],'phrase_groups':[]}}
    quality=evaluate_profile_quality(trip,places,itinerary,modules,{'interest_strength':{'food':.9}})
    assert quality['food']['food_city_coverage']==1
    assert not any(w['code']=='FOOD_CITY_COVERAGE_LOW' for w in quality['warnings'])
