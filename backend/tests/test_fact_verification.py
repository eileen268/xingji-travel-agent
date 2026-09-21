import json
import sqlite3
from contextlib import contextmanager

from app.fact_verification import verify_place_facts,verify_trip_facts
from app.migrations import migrate


def amap_place():
    return {'id':'amap_a','provider':'amap','provider_place_id':'A','canonical_name':'甘肃省博物馆',
            'city':'兰州','district':'七里河区','address':'西津西路','latitude':36.0,'longitude':103.7,
            'category':'科教文化服务;博物馆','source_confidence':.94,'verification_status':'partially_verified',
            'place_type':'sight','official_url':None,'map_url':'https://uri.amap.com/search?keyword=x',
            'hours_note':'开放时间出发前请复核。','ticket_note':'票务规则出发前请复核。',
            'reservation_note':'预约要求出发前请复核。','fact_provenance':{}}


def local_db(tmp_path):
    path=tmp_path/'facts.db';migrate(path)
    @contextmanager
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        try:yield db
        except BaseException:db.rollback();raise
        else:db.commit()
        finally:db.close()
    return connect


def test_amap_hard_facts_are_verified_and_dynamic_facts_need_recheck():
    place=amap_place();warnings=verify_place_facts(place,'2026-09-15T00:00:00+00:00')
    assert place['verification_status']=='verified' and not warnings
    assert place['fact_provenance']['latitude']['status']=='verified'
    assert place['fact_provenance']['opening_hours']['status']=='needs_recheck'
    assert place['fact_provenance']['official_url']['status']=='unresolved'


def test_generated_experience_is_not_presented_as_verified_place():
    place={**amap_place(),'id':'generated_x','provider':'llm_generated','provider_place_id':None,
           'place_type':'generated_experience','verification_status':'generated','map_url':None}
    warnings=verify_place_facts(place)
    assert place['verification_status']=='generated'
    assert warnings[0]['code']=='UNVERIFIED_EXPERIENCE'


def test_trip_fact_summary_counts_routes_and_hard_reference_errors():
    place=amap_place()
    itinerary=[{'city':'兰州','stops':[{'place_id':'amap_a','route_status':'not_applicable'},
                                      {'place_id':'missing','route_status':'estimated_by_distance'},
                                      {'place_id':'amap_a','route_status':'unresolved'}]}]
    summary=verify_trip_facts([place],itinerary)
    assert summary['estimated_route_count']==1 and summary['unresolved_route_count']==1
    assert summary['scheduled_real_poi_count']==1 and summary['resolved_real_poi_count']==1
    assert summary['route_segment_count']==2 and summary['fallback_route_count']==2
    assert summary['route_coverage_rate']==0
    assert summary['hard_fact_errors'][0]['code']=='INVALID_PLACE_REFERENCE'
    assert {w['code'] for w in summary['warnings']} >= {'ROUTE_ESTIMATED','ROUTE_UNRESOLVED','DYNAMIC_FACTS_NEED_RECHECK'}


def test_unresolved_route_warning_identifies_day_segment_and_reason():
    first=amap_place();second={**amap_place(),'id':'llm_b','provider':'llm_generated','provider_place_id':None,
        'canonical_name':'待解析餐厅','latitude':None,'longitude':None,'verification_status':'unverified'}
    itinerary=[{'city':'兰州','stops':[{'place_id':'amap_a'},{'place_id':'llm_b'}],
        'segments':[{'from_place_id':'amap_a','to_place_id':'llm_b','route_status':'unresolved',
                     'route_error_code':'ROUTE_INPUT_INVALID','fallback_schedule_minutes':30}]}]
    summary=verify_trip_facts([first,second],itinerary)
    warning=next(item for item in summary['warnings'] if item['code']=='ROUTE_SEGMENT_UNRESOLVED')
    assert warning['day']==1 and warning['place_id']=='llm_b'
    assert warning['metadata']['reason']=='ROUTE_INPUT_INVALID'
    assert warning['metadata']['fallback_schedule_minutes']==30


def test_fact_verification_run_is_persisted(tmp_path):
    connect=local_db(tmp_path);summary=verify_trip_facts([amap_place()],[{'city':'兰州','stops':[]}],
                                                        connect=connect,job_id='job',run_id='run')
    with connect() as db:
        row=db.execute('select * from fact_verification_runs where job_id=?',('job',)).fetchone()
    assert row['verifier_version']==summary['version']
    assert json.loads(row['summary_json'])['verified_place_count']==1


def test_dynamic_fact_metrics_only_count_final_scheduled_places():
    scheduled=amap_place();scheduled['official_facts']={name:{'status':'provider_verified','source_type':'amap'}
        for name in ('opening_hours','ticket_info','reservation_required')}
    unused={**amap_place(),'id':'unused','official_facts':{}}
    summary=verify_trip_facts([scheduled,unused],[{'city':'兰州','stops':[{'place_id':'amap_a'}],'segments':[]}])
    assert summary['scheduled_place_fact_coverage']==1
    assert summary['provider_fact_coverage']==1
    assert summary['needs_recheck_count']==0
    assert summary['scheduled_fact_details']==[{'place_id':'amap_a','place_name':'amap_a','needs_recheck':[]}]
