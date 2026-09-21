import asyncio,json
import httpx,pytest

from app.services.google_places import (GooglePlaceResolver,GooglePlacesClient,
    GooglePlacesConfigurationError)


def payload(place_id,name,address,lat,lng,types=None,website=None):
    value={'id':place_id,'displayName':{'text':name,'languageCode':'zh-CN'},'formattedAddress':address,
           'location':{'latitude':lat,'longitude':lng},'types':types or ['tourist_attraction'],
           'businessStatus':'OPERATIONAL'}
    if website:value['websiteUri']=website
    return value


def test_client_uses_minimal_field_masks_and_resolver_does_not_pick_first_candidate():
    requests=[]
    wrong=payload('wrong','上海博物馆','北京市东城区',39.9,116.4,['museum'])
    right=payload('right','上海博物馆','上海市黄浦区人民大道201号',31.2302,121.4704,['museum'])
    detail={**right,'websiteUri':'https://www.shanghaimuseum.cn/',
            'regularOpeningHours':{'weekdayDescriptions':['星期二至星期日 09:00–17:00']},
            'nationalPhoneNumber':'021-63723500'}
    async def handler(request):
        requests.append(request)
        if request.url.path.endswith('places:searchText'):return httpx.Response(200,json={'places':[wrong,right]})
        return httpx.Response(200,json=detail)
    client=GooglePlacesClient(key='test',transport=httpx.MockTransport(handler))
    result=asyncio.run(GooglePlaceResolver(client).resolve({'canonical_name':'上海博物馆','local_name':'上海博物馆',
        'city':'上海','address':'人民大道201号','latitude':31.2302,'longitude':121.4704,'category':'博物馆','place_type':'sight'}))
    assert result.status=='exact_match' and result.selected.google_place_id=='right'
    assert result.selected.website_uri=='https://www.shanghaimuseum.cn/'
    assert len(requests)==2 and requests[0].headers['x-goog-fieldmask']==client.SEARCH_MASK
    assert requests[1].headers['x-goog-fieldmask']==client.DETAILS_MASK
    assert 'websiteUri' not in client.SEARCH_MASK and 'websiteUri' in client.DETAILS_MASK


def test_close_top_candidates_are_ambiguous_and_details_are_not_called():
    async def handler(request):
        return httpx.Response(200,json={'places':[
            payload('a','中山桥','甘肃省兰州市城关区',36.06,103.82),
            payload('b','中山桥','甘肃省兰州市城关区',36.061,103.821)]})
    client=GooglePlacesClient(key='test',transport=httpx.MockTransport(handler))
    result=asyncio.run(GooglePlaceResolver(client).resolve({'canonical_name':'中山桥','local_name':'中山桥','city':'兰州',
        'address':'兰州市城关区','latitude':36.06,'longitude':103.82,'category':'风景名胜','place_type':'sight'}))
    assert result.status=='ambiguous' and result.selected is None
    assert len(client.metrics)==1


def test_generated_experience_is_skipped_without_google_request():
    async def handler(request):raise AssertionError('Google must not be called')
    client=GooglePlacesClient(key='test',transport=httpx.MockTransport(handler))
    result=asyncio.run(GooglePlaceResolver(client).resolve({'canonical_name':'兰州小吃制作体验','city':'兰州',
                                                            'place_type':'generated_experience'}))
    assert result.status=='skipped_generated_experience' and not client.metrics


def test_missing_key_is_explicit_configuration_error(monkeypatch):
    monkeypatch.delenv('GOOGLE_PLACES_API_KEY',raising=False)
    with pytest.raises(GooglePlacesConfigurationError):GooglePlacesClient()
