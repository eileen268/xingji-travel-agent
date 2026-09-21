"""Read-only audit of persisted valid pack payloads against canonical models."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.contracts import validate_pack_model
from app.main import database_path

def main():
    db=sqlite3.connect(database_path());db.row_factory=sqlite3.Row
    rows=db.execute("SELECT job_id,pack_id,payload FROM job_packs WHERE status='valid' AND payload IS NOT NULL").fetchall()
    failures=[];counts=Counter()
    for row in rows:
        try:validate_pack_model(row['pack_id'],json.loads(row['payload']))
        except Exception as exc:
            counts[row['pack_id']]+=1
            details=[]
            if hasattr(exc,'errors'):
                details=[{'path':'/'+ '/'.join(map(str,error['loc'])),'type':error['type'],'message':error['msg']}
                         for error in exc.errors(include_url=False,include_input=False)[:8]]
            failures.append({'job_id':row['job_id'],'pack_id':row['pack_id'],'error_type':type(exc).__name__,'details':details})
    print(json.dumps({'valid_payloads_checked':len(rows),'incompatible_count':len(failures),
                      'incompatible_by_pack':dict(counts),'failures':failures},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
