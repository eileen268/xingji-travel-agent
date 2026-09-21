from __future__ import annotations

import json
from typing import Iterable

from .extractor import FactCandidate


SOURCE_PRIORITY={'official_website':4,'official_booking':4,'amap':3,'trusted_third_party':2,'llm':1}


def _normalized(value):
    if isinstance(value,bool):return value
    return ''.join(str(value).split()).casefold().replace('：',':').replace('—','-').replace('～','-')


class FactMerger:
    def merge(self,candidates: Iterable[FactCandidate],fact_names: Iterable[str]) -> dict[str,dict]:
        grouped={name:[] for name in fact_names}
        for item in candidates:grouped.setdefault(item.fact_name,[]).append(item)
        result={}
        for name,items in grouped.items():
            if not items:
                result[name]={'value':None,'source_url':None,'source_type':None,'extracted_at':None,
                              'confidence':0.0,'raw_evidence':'','status':'unavailable','sources':[]}
                continue
            ordered=sorted(items,key=lambda item:(SOURCE_PRIORITY[item.source_type],item.confidence),reverse=True)
            winner=ordered[0];distinct={json.dumps(_normalized(item.value),ensure_ascii=False,sort_keys=True) for item in ordered}
            conflict=len(distinct)>1
            if conflict:status='conflicting'
            elif winner.source_type in ('official_website','official_booking') and winner.confidence>=.8:status='verified'
            elif winner.source_type=='amap' and (
                name in ('provider_rating','provider_cost') or
                (name=='opening_hours' and isinstance(winner.value,dict) and winner.value.get('weekly'))
            ):status='provider_verified'
            elif winner.source_type=='amap':status='partially_verified'
            else:status='needs_recheck'
            winner_payload=winner.model_dump(mode='json')
            # fact_name is the dictionary key and remains on provenance source
            # records. Repeating it on the resolved value violates the canonical
            # ResolvedPlaceFact contract.
            winner_payload.pop('fact_name',None)
            result[name]={**winner_payload,'status':status,
                          'sources':[item.model_dump(mode='json') for item in ordered]}
        return result
