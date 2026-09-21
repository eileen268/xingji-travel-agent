from app.experience_policy import apply_experience_policy,experience_status,is_schedulable_experience


def test_unbound_llm_experience_is_suggestion_and_not_schedulable():
    place={'id':'generated_one','type':'experience','place_type':'generated_experience',
           'entity_kind':'experience_concept','linked_place_id':None,'user_created':False}
    assert experience_status(place)=='suggested_experience'
    assert not is_schedulable_experience(place)


def test_amap_backed_experience_is_verified_and_schedulable():
    place={'id':'amap_one','type':'experience','place_type':'experience','entity_kind':'poi',
           'provider':'amap','provider_place_id':'B001','latitude':31.2,'longitude':121.4}
    apply_experience_policy([place])
    assert place['experience_status']=='verified_experience'
    assert is_schedulable_experience(place)


def test_user_created_activity_remains_schedulable_without_provider():
    place={'id':'custom_one','type':'experience','place_type':'generated_experience',
           'entity_kind':'experience_concept','linked_place_id':None,'user_created':True}
    assert is_schedulable_experience(place)
