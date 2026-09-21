"""Export checked-in JSON Schemas from the canonical Pydantic models."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.contracts import PACK_CONTRACT_VERSIONS
from app.models import DestinationProfile,pack_model
from app.versions import SCHEMA_VERSION


def write(path: Path,payload: dict):
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    schema_root=ROOT/'schemas'
    research={
        'schema_version':SCHEMA_VERSION,
        'contract_versions':PACK_CONTRACT_VERSIONS,
        'packs':{pack_id:pack_model(pack_id).model_json_schema() for pack_id in PACK_CONTRACT_VERSIONS},
    }
    destination=DestinationProfile.model_json_schema()
    destination['$schema']='https://json-schema.org/draft/2020-12/schema'
    destination['$comment']='Derived from MIT personalized-travel-guide-skill at 7372e476; see DERIVATION.md. Seven modules; no shopping module; one menu_guide.'
    write(schema_root/'research-pack-contract.seven.json',research)
    write(schema_root/'destination-profile.seven.schema.json',destination)
