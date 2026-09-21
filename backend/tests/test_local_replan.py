import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app, connect


PREFS = dict(
    origin='上海', destinations=['杭州'], mainDestination='杭州',
    startDate='2026-10-02', endDate='2026-10-06', adults=2, children=0, seniors=0,
    travelStyle='朋友旅行', budget={'total': 10000},
    preferences={'pace': 'relaxed', 'interests': ['历史文化'], 'localTransport': ['transit']},
    outboundPeriod='morning', returnPeriod='evening',
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'replan.db'))
    monkeypatch.setenv('ENABLE_DEV_MODES', '1')
    monkeypatch.setenv('ZHIPU_API_KEY', '')
    monkeypatch.setattr('app.replan._route_service', lambda connect: None)
    with TestClient(app) as value:
        yield value


def _finish(client, job_id):
    for _ in range(150):
        state = client.get(f'/api/build/{job_id}').json()
        if state['status'] in ('done', 'done_with_warnings', 'paused', 'failed'):
            return state
        time.sleep(.02)
    pytest.fail('任务未完成')


def _job(client):
    response = client.post('/api/build', json=PREFS, headers={'X-Agent-Mode': 'MOCK'})
    job_id = response.json()['job_id']
    assert _finish(client, job_id)['status'] == 'done_with_warnings'
    return job_id


def _allocation(job_id):
    with connect() as db:
        return json.loads(db.execute("SELECT payload FROM job_packs WHERE job_id=? AND pack_id='itinerary-allocation'",
                                     (job_id,)).fetchone()['payload'])


def _replaceable(profile, allocation):
    places={place['id']:place for place in profile['places']}
    for day in allocation['days']:
        actual = [stop['place_id'] for stop in profile['itinerary'][day['day'] - 1]['stops']]
        candidates = [value for value in [*day['preferred_candidate_ids'], *day['backup_candidate_ids']]
                      if places[value].get('experience_status')!='suggested_experience']
        old = next((value for value in actual if value in candidates), None)
        new = next((value for value in candidates if value not in actual), None)
        if old and new:
            return day['day'], old, new
    pytest.fail('fixture 没有可替换的同日候选')


def test_replace_stop_is_scoped_idempotent_and_reuses_research(client):
    job_id = _job(client); before = client.get(f'/api/build/{job_id}/result').json()
    allocation = _allocation(job_id); day, old, new = _replaceable(before, allocation)
    with connect() as db:
        research_before = {name: db.execute('SELECT payload FROM job_packs WHERE job_id=? AND pack_id=?',
                           (job_id, name)).fetchone()['payload'] for name in ('places-core', 'places-experiences', 'places-food')}
    headers = {'Idempotency-Key': 'replace-once'}
    payload = {'action': 'replace_stop', 'day': day, 'place_id': old, 'replacement_place_id': new}
    first = client.post(f'/api/trips/{job_id}/replan', json=payload, headers=headers)
    assert first.status_code == 200, first.text
    after = client.get(f'/api/build/{job_id}/result').json()
    ids = [stop['place_id'] for stop in after['itinerary'][day - 1]['stops']]
    assert new in ids and old not in ids
    for index in range(len(before['itinerary'])):
        if index != day - 1:
            assert before['itinerary'][index] == after['itinerary'][index]
    with connect() as db:
        research_after = {name: db.execute('SELECT payload FROM job_packs WHERE job_id=? AND pack_id=?',
                          (job_id, name)).fetchone()['payload'] for name in research_before}
    assert research_after == research_before
    second = client.post(f'/api/trips/{job_id}/replan', json=payload, headers=headers)
    assert second.status_code == 200 and second.json()['idempotent_replay'] is True
    assert second.json()['replan_id'] == first.json()['replan_id']
    assert len(client.get(f'/api/trips/{job_id}/replans').json()['items']) == 1


def test_replan_options_only_exposes_unused_day_candidates(client):
    job_id = _job(client)
    profile = client.get(f'/api/build/{job_id}/result').json()
    allocation = _allocation(job_id)
    day, old, new = _replaceable(profile, allocation)
    response = client.get(f'/api/trips/{job_id}/replan-options', params={'day': day})
    assert response.status_code == 200, response.text
    payload = response.json()
    scheduled = {stop['place_id'] for stop in profile['itinerary'][day - 1]['stops']}
    exposed = {item['place_id'] for item in payload['replacement_candidates']}
    assert new in exposed
    assert not exposed & scheduled
    current = next(item for item in payload['stops'] if item['place_id'] == old)
    assert current['replaceable'] is True
    anchor = allocation['days'][day - 1]['primary_anchor_id']
    anchor_option = next(item for item in payload['stops'] if item['place_id'] == anchor)
    assert anchor_option['removable'] is False and anchor_option['replaceable'] is False


def test_remove_stop_releases_ownership_and_keeps_other_days(client):
    job_id = _job(client); before = client.get(f'/api/build/{job_id}/result').json()
    allocation = _allocation(job_id); day, old, _ = _replaceable(before, allocation)
    response = client.post(f'/api/trips/{job_id}/replan', json={'action': 'remove_stop', 'day': day, 'place_id': old})
    assert response.status_code == 200, response.text
    after = client.get(f'/api/build/{job_id}/result').json(); updated_allocation = _allocation(job_id)
    assert old not in [stop['place_id'] for stop in after['itinerary'][day - 1]['stops']]
    assert old not in updated_allocation['place_assignments']
    assert old in updated_allocation['available_place_ids']
    assert all(before['itinerary'][index] == after['itinerary'][index]
               for index in range(len(before['itinerary'])) if index != day - 1)


def test_immutable_anchor_rejection_restores_completed_job(client):
    job_id = _job(client); allocation = _allocation(job_id); anchor = allocation['days'][0]['primary_anchor_id']
    response = client.post(f'/api/trips/{job_id}/replan',
                           json={'action': 'remove_stop', 'day': 1, 'place_id': anchor})
    assert response.status_code == 422
    state = client.get(f'/api/build/{job_id}').json()
    assert state['status'] == 'done_with_warnings' and state['stage'] == 'done'
    history = client.get(f'/api/trips/{job_id}/replans').json()['items']
    assert history[0]['status'] == 'failed' and history[0]['error']['code'] == 'IMMUTABLE_STOP'


def test_change_pace_reschedules_all_days_without_research(client):
    job_id = _job(client)
    with connect() as db:
        before = {name: db.execute('SELECT payload FROM job_packs WHERE job_id=? AND pack_id=?',
                  (job_id, name)).fetchone()['payload'] for name in ('places-core', 'places-experiences', 'places-food')}
    response = client.post(f'/api/trips/{job_id}/replan', json={'action': 'change_pace', 'pace': 'intensive'})
    assert response.status_code == 200, response.text
    assert response.json()['affected_days'] == [1, 2, 3, 4, 5]
    result = client.get(f'/api/build/{job_id}/result').json()
    assert result['trip']['preferences']['pace'] == 'intensive'
    with connect() as db:
        after = {name: db.execute('SELECT payload FROM job_packs WHERE job_id=? AND pack_id=?',
                 (job_id, name)).fetchone()['payload'] for name in before}
    assert after == before


def test_regenerate_one_day_keeps_other_days_and_does_not_call_llm(client):
    job_id = _job(client); before = client.get(f'/api/build/{job_id}/result').json()
    response = client.post(f'/api/trips/{job_id}/replan', json={'action': 'regenerate_day', 'day': 2})
    assert response.status_code == 200, response.text
    assert response.json()['affected_days'] == [2]
    after = client.get(f'/api/build/{job_id}/result').json()
    assert all(before['itinerary'][index] == after['itinerary'][index]
               for index in range(len(before['itinerary'])) if index != 1)
    with connect() as db:
        new_requests = db.execute("SELECT COUNT(*) count FROM llm_subrequests WHERE job_id=? AND started_at>?",
            (job_id, before['generation']['created_at'])).fetchone()['count']
    assert new_requests == 0


def test_change_transport_reschedules_without_research_or_llm(client):
    job_id = _job(client)
    with connect() as db:
        research_before = {name: db.execute('SELECT payload FROM job_packs WHERE job_id=? AND pack_id=?',
                           (job_id, name)).fetchone()['payload'] for name in ('places-core', 'places-experiences', 'places-food')}
    response = client.post(f'/api/trips/{job_id}/replan',
                           json={'action': 'change_transport', 'local_transport': ['self_drive']})
    assert response.status_code == 200, response.text
    result = client.get(f'/api/build/{job_id}/result').json()
    assert result['trip']['preferences']['localTransport'] == ['self_drive']
    with connect() as db:
        research_after = {name: db.execute('SELECT payload FROM job_packs WHERE job_id=? AND pack_id=?',
                          (job_id, name)).fetchone()['payload'] for name in research_before}
        llm_count = db.execute('SELECT COUNT(*) count FROM llm_subrequests WHERE job_id=?',(job_id,)).fetchone()['count']
    assert research_after == research_before and llm_count == 0


def test_replace_rejects_candidate_outside_day_pool(client):
    job_id = _job(client); profile = client.get(f'/api/build/{job_id}/result').json(); allocation = _allocation(job_id)
    day, old, _ = _replaceable(profile, allocation)
    response = client.post(f'/api/trips/{job_id}/replan', json={
        'action': 'replace_stop', 'day': day, 'place_id': old, 'replacement_place_id': 'unknown-place',
    })
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'REPLACEMENT_OUTSIDE_DAY_POOL'
