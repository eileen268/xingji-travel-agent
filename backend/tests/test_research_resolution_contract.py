from jsonschema import Draft202012Validator

from app.contracts import research_batch_model
from app.llm_chunks import compact_research_batch


def test_generated_experience_status_is_deferred_until_resolution():
    raw={'places':[{'id':'e0-0-1','type':'experience','city':'兰州',
        'display_name':'Lanzhou Traditional Snacks Making Workshop','local_name':'兰州传统小吃制作工坊',
        'description':'体验构想','cuisine':None,'signature_dishes':None,'experience_type':'food_workshop'}]}
    compact=compact_research_batch(raw)
    assert compact['places'][0]['verification_status']=='unverified'
    assert compact['places'][0]['cuisine']=='' and compact['places'][0]['signature_dishes']==''
    model=research_batch_model('兰州','experience',1)
    assert not list(Draft202012Validator(model.model_json_schema()).iter_errors(compact))
    model.model_validate(compact)
