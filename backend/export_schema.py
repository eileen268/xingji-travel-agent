"""Regenerate checked-in schemas from the Pydantic v2 source of truth."""
import json
from pathlib import Path
from app.models import Day, DestinationProfile, PACK_MODELS
from app.contracts import PACK_CONTRACT_VERSIONS

root=Path(__file__).parent/'schemas'
root.mkdir(exist_ok=True)
schema=DestinationProfile.model_json_schema()
schema['$schema']='https://json-schema.org/draft/2020-12/schema'
schema['$comment']='Derived from MIT personalized-travel-guide-skill at 7372e476; see DERIVATION.md. Seven modules; no shopping module; one menu_guide.'
(root/'destination-profile.seven.schema.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(root/'research-pack-contract.seven.json').write_text(json.dumps({
    'schema_version':'seven-v1',
    'contract_versions':PACK_CONTRACT_VERSIONS,
    'packs':{**{k:v.model_json_schema() for k,v in PACK_MODELS.items()},'itinerary-day-*':Day.model_json_schema()},
},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
