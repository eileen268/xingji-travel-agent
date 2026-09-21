import copy

from app.models import BuildInput, DestinationProfile
from app.offline import make_packs
from app.pipeline import assemble
from app.warnings import dedupe_warnings
from tests.test_build import PREFS


def messages(values):
    return [item['message'] for item in values]


def test_legacy_string_warnings_normalize_to_canonical_records():
    warnings = dedupe_warnings(('build', ['A', 'B']))
    assert messages(warnings) == ['A', 'B']
    assert all(item['code'] == 'GENERAL_WARNING' and item['severity'] == 'warning' for item in warnings)


def test_structured_warnings_preserve_their_fields():
    warnings = dedupe_warnings(('fact', [
        {'code': 'X', 'message': 'xxx', 'pointer': '/places/a', 'metadata': {'source': 'amap'}},
        {'code': 'Y', 'message': 'yyy'},
    ]))
    assert [item['code'] for item in warnings] == ['X', 'Y']
    assert warnings[0]['pointer'] == '/places/a'
    assert warnings[0]['metadata'] == {'source': 'amap'}


def test_mixed_warning_sources_merge_without_hash_errors():
    warnings = dedupe_warnings(
        ('build', ['A']),
        ('schedule', [{'code': 'X', 'message': 'xxx'}]),
        ('enrichment', ['B']),
    )
    assert messages(warnings) == ['A', 'xxx', 'B']


def test_structured_warning_dedup_ignores_dictionary_key_order():
    warnings = dedupe_warnings(('fact', [
        {'code': 'ROUTE_UNRESOLVED', 'message': '路线待计算'},
        {'message': '路线待计算', 'code': 'ROUTE_UNRESOLVED'},
    ]))
    assert len(warnings) == 1
    assert warnings[0]['code'] == 'ROUTE_UNRESOLVED'


def test_assemble_normalizes_resolution_gate_warning_and_validates_profile():
    trip = BuildInput(**PREFS)
    packs = copy.deepcopy(make_packs(trip))
    # Persisted packs from versions before WarningRecord legitimately contain
    # text schedule warnings.  Assemble is the explicit compatibility edge.
    packs['itinerary']['itinerary'][0]['schedule_warnings'] = ['当天安排偏松']
    packs['enrichment_warnings'] = [
        {'code': 'REAL_POI_UNRESOLVED', 'message': '测试地点未能可靠绑定高德 POI。',
         'place_id': 'sights-0-0-0', 'metadata': {'resolution_status': 'REAL_POI_UNRESOLVED'}},
        '语言补充内容待生成。',
    ]
    profile = assemble(trip, 'warning-contract', packs, 'mock', ['初始提示'])
    DestinationProfile.model_validate(profile)
    assert {item['code'] for item in profile['generation']['warnings']} >= {
        'NEEDS_CONFIRMATION', 'DYNAMIC_INFORMATION'
    }
    assert {item['code'] for item in profile['generation']['diagnostics']} >= {
        'REAL_POI_UNRESOLVED', 'GENERAL_WARNING'
    }
    assert all(isinstance(item, dict) for item in profile['generation']['warnings'])
    assert profile['itinerary'][0]['schedule_warnings'][0]['message'] == '当天安排偏松'


def test_user_warning_aggregation_hides_duplicate_root_cause_diagnostics():
    from app.warnings import user_warnings
    places=[{'id':'p1','display_name':'测试馆'}]
    facts={'warnings':[
        {'code':'UNRESOLVED_REAL_POI','place_id':'p1','message':'未绑定'},
        {'code':'ROUTE_SEGMENT_UNRESOLVED','place_id':'p1','message':'路线缺失'},
    ],'scheduled_fact_details':[]}
    result=user_warnings(facts['warnings'],places,[],facts)
    assert len(result)==1 and result[0]['code']=='NEEDS_CONFIRMATION'
    assert '测试馆' in result[0]['message']


def test_schedule_warning_legacy_text_is_normalized_by_day_contract():
    trip = BuildInput(**PREFS)
    day = make_packs(trip)['itinerary']['itinerary'][0]
    day['schedule_warnings'] = ['当天安排偏松']
    from app.models import Day
    parsed = Day.model_validate(day).model_dump(mode='json')
    assert parsed['schedule_warnings'][0]['message'] == '当天安排偏松'
