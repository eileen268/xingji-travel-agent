import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.food_contract import (
    FOOD_MAX_PER_CITY, FOOD_MIN_PER_CITY, food_batch_instruction, food_batch_model,
    normalize_food_batch,
)
from app.interests import enrich_place
from app.llm_chunks import ChunkRunner, StageValidationError
from app.models import BuildInput
from app.offline import make_packs
from tests.test_build import PREFS


def restaurants(count=8, city='张掖'):
    source=next(p for p in make_packs(BuildInput(**PREFS))['places-core']['places'] if p['type']=='restaurant')
    result=[]
    for index in range(count):
        item=copy.deepcopy(source)
        item.update(id=f'r-{index}',city=city,display_name=f'{city}餐厅{index}',local_name=f'{city}餐厅{index}',
                    cuisine=f'菜系{index}',semantic_tags=[],interest_affinity={},semantic_source=[])
        item.pop('semantic_version',None)
        keep=('id','type','city','display_name','local_name','description','cuisine','signature_dishes','experience_type')
        result.append({key:item[key] for key in keep})
    return result


def test_single_city_four_restaurants_match_shared_contract():
    model=food_batch_model('张掖')
    assert len(model.model_validate({'places':restaurants(4)}).places)==4
    schema=model.model_json_schema()['properties']['places']
    assert (schema['minItems'],schema['maxItems'])==(FOOD_MIN_PER_CITY,FOOD_MAX_PER_CITY)==(2,6)
    prompt=food_batch_instruction('张掖',1)
    assert str(FOOD_MIN_PER_CITY) in prompt and str(FOOD_MAX_PER_CITY) in prompt


def test_food_research_schema_stays_compact():
    schema=food_batch_model('张掖').model_json_schema()
    item_schema=schema['$defs']['RestaurantBatchPlace']
    properties=item_schema['properties']
    assert item_schema['additionalProperties'] is False
    assert properties['description']['maxLength']==600
    assert not ({'interest_affinity','affinity_reasons','semantic_source','semantic_confidence'} & properties.keys())


def test_multicity_batches_are_bounded_per_city_not_by_trip_total():
    batches=[]
    for city in ('兰州','张掖','敦煌'):
        batches.extend(food_batch_model(city).model_validate({'places':restaurants(4,city)}).places)
    assert len(batches)==12


def test_food_overproduction_is_deterministically_trimmed_without_invention():
    original=restaurants(8);ids={item['id'] for item in original}
    normalized=normalize_food_batch({'places':copy.deepcopy(original)},'张掖')
    assert len(normalized['places'])==FOOD_MAX_PER_CITY
    assert {item['id'] for item in normalized['places']}<=ids
    assert len({item['cuisine'] for item in normalized['places']})==FOOD_MAX_PER_CITY
    food_batch_model('张掖').model_validate(normalized)


def test_food_underproduction_remains_invalid_for_targeted_repair():
    with pytest.raises(ValidationError):
        food_batch_model('张掖').model_validate({'places':restaurants(1)})


def test_overproduction_is_trimmed_before_schema_without_using_repair():
    class Client:
        def __init__(self):self.calls=0;self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            self.calls+=1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'places':restaurants(7)}, ensure_ascii=False)))])
    client=Client();runner=ChunkRunner(client,'system',BuildInput(**PREFS),'model')
    model=food_batch_model('张掖')
    result=asyncio.run(runner.ask('restaurants-0',model,'test',canonicalize=lambda data:normalize_food_batch(data,'张掖')))
    assert len(result['places'])==FOOD_MAX_PER_CITY
    assert client.calls==1 and runner.records['restaurants-0']['attempts']==1


def test_underproduction_uses_the_single_targeted_repair():
    class Client:
        def __init__(self):self.calls=0;self.chat=SimpleNamespace(completions=self)
        async def create(self,**kwargs):
            self.calls+=1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'places':restaurants(1)}, ensure_ascii=False)))])
    client=Client();runner=ChunkRunner(client,'system',BuildInput(**PREFS),'model')
    with pytest.raises(StageValidationError):
        asyncio.run(runner.ask('restaurants-0',food_batch_model('张掖'),'test',canonicalize=lambda data:normalize_food_batch(data,'张掖')))
    assert client.calls==2 and runner.records['restaurants-0']['attempts']==2


def test_restaurant_affinity_is_type_aware_and_capped():
    vegan=restaurants(1)[0]
    vegan.update(display_name='普通素食餐厅',local_name='普通素食餐厅',
                 description='这是一家强调环保理念的普通素食餐厅，供应日常素食套餐，具体菜单和接待情况出发前请复核。',
                 semantic_tags=['natural_scenic'])
    noodle=restaurants(1)[0]
    noodle.update(id='noodle',display_name='普通面馆',local_name='普通面馆',
                  description='这是一家普通面馆，供应常见面食，门店以丝路主题装饰，实际菜单和营业情况出发前请复核。',
                  semantic_tags=['historic_site'])
    vegan=enrich_place(vegan);noodle=enrich_place(noodle)
    assert vegan['interest_affinity'].get('natural_scenery',0)<=.1
    assert noodle['interest_affinity'].get('history_culture',0)<=.3
    for place in (vegan,noodle):
        assert place['interest_affinity'].get('photo_atmosphere',0)<=.3
        assert place['interest_affinity'].get('outdoor',0)<=.1

