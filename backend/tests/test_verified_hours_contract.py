import copy

from app.fact_verification import verify_place_facts
from app.fact_verification import normalize_dynamic_hours
from app.models import BuildInput
from app.offline import make_packs
from app.pipeline import assemble
from app.pipeline import failure_reason
from app.llm_chunks import StageValidationError
from app.validators import contains_specific_opening_time, validate_destination_data


PREFS = dict(
    origin='上海', destinations=['杭州'], mainDestination='杭州',
    startDate='2026-10-02', endDate='2026-10-06', adults=2, children=0, seniors=0,
    travelStyle='朋友旅行', budget={'total': 10000},
    preferences={'pace': 'balanced', 'interests': ['历史文化'], 'localTransport': ['transit']},
    outboundPeriod='morning', returnPeriod='evening',
)


def _profile():
    trip = BuildInput(**PREFS)
    packs = make_packs(trip)
    return assemble(trip, 'verified-hours-test', packs, 'offline', [])


def _source(provider, value, *, url='https://www.amap.com/'):
    source_type = 'amap' if provider == 'amap' else 'official_website'
    return {
        'fact_name': 'opening_hours', 'value': value, 'source_url': url,
        'source_type': source_type, 'extracted_at': '2026-09-15T00:00:00+00:00',
        'confidence': .9, 'raw_evidence': str(value),
        'provider_place_id': 'B0TEST' if provider == 'amap' else None,
    }


def _opening_fact(status, value, sources):
    selected = sources[0] if sources else None
    return {
        'value': value,
        'source_url': selected['source_url'] if selected else None,
        'source_type': selected['source_type'] if selected else None,
        'extracted_at': selected['extracted_at'] if selected else None,
        'confidence': selected['confidence'] if selected else .9,
        'raw_evidence': selected['raw_evidence'] if selected else str(value),
        'status': status, 'provider_place_id': selected.get('provider_place_id') if selected else None,
        'sources': sources,
    }


def _hours_errors(profile):
    return [error for error in validate_destination_data(profile)
            if error['pointer'].endswith('/hours_note')]


def _set_opening_fact(place, fact):
    place.setdefault('official_facts', {})['opening_hours'] = fact


def test_provider_verified_numeric_hours_pass():
    profile = _profile(); place = profile['places'][0]
    value = {'today': '08:30-17:00', 'weekly': '周一至周日 08:30-17:00'}
    place['hours_note'] = value['weekly']
    _set_opening_fact(place, _opening_fact('provider_verified', value, [_source('amap', value)]))
    assert not _hours_errors(profile)


def test_official_verified_numeric_hours_pass():
    profile = _profile(); place = profile['places'][0]
    place['hours_note'] = '09:00-16:30'
    _set_opening_fact(place, _opening_fact(
        'verified', '09:00-16:30', [_source('official', '09:00-16:30', url='https://example.org/visit')]))
    assert not _hours_errors(profile)


def test_conflicting_hours_pass_and_emit_warning():
    profile = _profile(); place = profile['places'][0]
    place['hours_note'] = '09:00-16:30；不同来源营业时间存在差异，请以官方最新信息为准'
    sources = [_source('official', '09:00-16:30', url='https://example.org/visit'),
               _source('amap', {'today': '08:30-17:00', 'weekly': '08:30-17:00'})]
    _set_opening_fact(place, _opening_fact('conflicting', '09:00-16:30', sources))
    assert not _hours_errors(profile)
    warnings = verify_place_facts(copy.deepcopy(place))
    assert any(item['code'] == 'HOURS_SOURCE_CONFLICT' for item in warnings)


def test_unverified_numeric_hours_fail_with_actual_value():
    profile = _profile(); place = profile['places'][0]
    place['hours_note'] = '每天 09:00-17:00'
    errors = _hours_errors(profile)
    assert errors[0]['code'] == 'UNVERIFIED_DYNAMIC_TIME'
    assert errors[0]['invalid_value'] == '每天 09:00-17:00'
    assert errors[0]['context'] == {'hours_status': 'unknown', 'source_count': 0}


def test_verified_without_source_fails():
    profile = _profile(); place = profile['places'][0]
    place['hours_note'] = '09:00-17:00'
    _set_opening_fact(place, _opening_fact('verified', '09:00-17:00', []))
    errors = _hours_errors(profile)
    assert errors[0]['code'] == 'VERIFIED_FACT_MISSING_SOURCE'


def test_unknown_conservative_fallback_and_static_number_pass():
    profile = _profile(); place = profile['places'][0]
    place['hours_note'] = '地铁2号线附近，营业时间可能调整，建议出发前通过官方渠道复核'
    assert not contains_specific_opening_time(place['hours_note'])
    assert not _hours_errors(profile)


def test_unknown_numeric_hours_are_normalized_before_profile_validation():
    profile=_profile();place=profile['places'][0]
    place['hours_note']='每天 09:00-17:00';place.setdefault('official_facts',{}).pop('opening_hours',None)
    assert normalize_dynamic_hours(place) is True
    assert place['hours_note']=='开放时间尚未核实，出发前请复核。'
    assert place['source_metadata']['discarded_unverified_hours_note']=='每天 09:00-17:00'
    assert not _hours_errors(profile)


def test_deterministic_profile_contract_failure_does_not_claim_llm_repair():
    error = StageValidationError('destination-profile', [{
        'pointer': '/places/x/hours_note', 'code': 'VALIDATOR_CONTRACT_MISMATCH',
        'message': 'test',
    }], generation_attempts=0, repair_attempts=0)
    assert error.generation_attempts == 0 and error.repair_attempts == 0
    message = failure_reason(error)
    assert '确定性' in message and '智谱生成内容' not in message
