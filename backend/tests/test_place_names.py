from app.models import BuildInput
from app.offline import make_packs
from app.pipeline import assemble
from app.place_allocator import build_canonical_place_pool
from app.place_names import normalize_place_names, normalize_profile_names
from app.validators import validate_destination_data


PREFS={
    'origin':'上海','destinations':['杭州'],'mainDestination':'杭州',
    'startDate':'2026-10-01','endDate':'2026-10-03',
    'adults':2,'children':0,'seniors':0,'outboundPeriod':'morning',
    'returnPeriod':'evening','travelStyle':'朋友出行','budget':{'total':6000},
    'preferences':{'interests':['美食'],'pace':'relaxed','longDistance':'high_speed_rail',
    'avoid':'','localTransport':['transit']},
}


def provider_place(place_type='sight'):
    return {
        'id':'sights-0-0-1' if place_type=='sight' else 'experiences-0-0-1',
        'type':place_type,'city':'兰州','display_name':'Zhengning Road Night Market',
        'local_name':'正宁路夜市','description':'本地饮食街区，具体营业情况出发前请复核。',
        'official_url':None,'map_url':'https://example.invalid/old','coordinates':None,
        'rating':None,'review_count':None,'hours_note':'开放安排出发前请复核',
        'ticket_note':'费用出发前请复核','reservation_note':'预约要求出发前请复核',
        'recheck_note':'出发前请复核','knowledge_status':'model_knowledge',
        'cuisine':'','signature_dishes':'','experience_type':'food',
    }


def test_chinese_locale_uses_local_name_and_preserves_english_alias():
    place=normalize_place_names(provider_place(),provider_output=True)
    assert place['canonical_name']=='正宁路夜市'
    assert place['local_name']=='正宁路夜市'
    assert place['english_name']=='Zhengning Road Night Market'
    assert place['display_name']=='正宁路夜市'
    assert '%E6%AD%A3%E5%AE%81%E8%B7%AF%E5%A4%9C%E5%B8%82' in place['map_url']


def test_unverified_generated_experience_is_not_a_real_poi_or_booking():
    raw=provider_place('experience')
    raw.update(display_name='Lanzhou Traditional Snacks Making Workshop',local_name='兰州传统小吃制作工坊')
    place=normalize_place_names(raw,provider_output=True)
    assert place['display_name']=='兰州传统小吃制作工坊'
    assert place['experience_title']=='兰州传统小吃制作工坊'
    assert place['entity_kind']=='experience_concept'
    assert place['linked_place_id'] is None
    assert place['booking_status']=='unverified'
    assert place['map_url'] is None
    assert place['id'] not in build_canonical_place_pool([place])


def test_profile_compiler_and_legacy_reader_replace_stale_english_names():
    trip=BuildInput(**PREFS)
    profile=assemble(trip,'names-test',make_packs(trip),'offline',[])
    place=profile['places'][0]
    old=place['display_name']
    place.update(display_name='West Lake Lakeside',local_name=old,english_name=None,canonical_name=old)
    profile['itinerary'][0]['summary']='Visit West Lake Lakeside slowly.'
    normalized=normalize_profile_names(profile)
    assert normalized['places'][0]['display_name']==old
    assert normalized['places'][0]['english_name']=='West Lake Lakeside'
    assert 'West Lake Lakeside' not in normalized['itinerary'][0]['summary']
    assert not validate_destination_data(normalized)


def test_provider_taxonomy_does_not_create_a_false_shopping_module_failure():
    trip=BuildInput(**PREFS);profile=assemble(trip,'provider-category',make_packs(trip),'offline',[])
    profile['places'][0]['category']='购物服务;特色商业街|风景名胜;旅游景点'
    assert not validate_destination_data(profile)


def test_mixed_chinese_provider_name_is_valid_local_display_name():
    raw=provider_place('restaurant')
    raw.update(canonical_name='老上海(CENTRAL PLAZA店)',local_name='老上海(CENTRAL PLAZA店)',
               display_name='老上海(CENTRAL PLAZA店)',english_name=None)
    place=normalize_place_names(raw)
    assert place['display_name']==place['local_name']
    assert place['english_name'] is None
