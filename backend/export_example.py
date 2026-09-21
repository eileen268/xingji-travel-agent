import json,asyncio,os
from pathlib import Path
from app.models import BuildInput
from app.pipeline import generate
from app.validators import check_handoff
root=Path(__file__).parent
os.environ['ZHIPU_API_KEY']=''
t=BuildInput.model_validate_json((root/'examples/hangzhou-preferences.json').read_text(encoding='utf-8'))
p,packs=asyncio.run(generate(t,'hangzhou-offline-example',lambda *args:None))
assert not check_handoff(p,packs)
(root/'examples/hangzhou-result.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Validated seven-module example:',len(p['places']),'places,',len(p['itinerary']),'days')
