"""Run the fixed Phase-A benchmark through REAL_LLM, MOCK, or REPLAY."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.models import BuildInput

ROOT=Path(__file__).resolve().parent
TERMINAL={"done","done_with_warnings","paused","failed"}


def request(url, method="GET", body=None, headers=None):
    data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
    req=urllib.request.Request(url,data=data,method=method,headers={"Content-Type":"application/json",**(headers or {})})
    try:
        with urllib.request.urlopen(req,timeout=30) as response: return response.status,json.load(response)
    except urllib.error.HTTPError as exc: return exc.code,json.load(exc)


def load_cases():
    cases=[]
    for path in sorted((ROOT/"benchmark"/"cases").glob("*.json")):
        case=json.loads(path.read_text(encoding="utf-8")); BuildInput.model_validate(case["input"]); cases.append(case)
    return cases


def poll(base_url,job_id,timeout):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        _,state=request(f"{base_url}/api/build/{job_id}")
        if state.get("status") in TERMINAL:return state
        time.sleep(1)
    return {"status":"timeout","job_id":job_id}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("REAL_LLM","MOCK","REPLAY"),default="MOCK")
    parser.add_argument("--base-url",default="http://127.0.0.1:8000")
    parser.add_argument("--source-map",help="JSON map from case name to completed job_id for REPLAY")
    parser.add_argument("--timeout",type=int,default=900)
    args=parser.parse_args(); cases=load_cases(); source_map={}
    if args.source_map: source_map=json.loads(Path(args.source_map).read_text(encoding="utf-8"))
    rows=[]
    for case in cases:
        started=time.monotonic()
        if args.mode=="REPLAY":
            source=source_map.get(case["name"])
            if not source:
                rows.append({"case":case["name"],"status":"skipped","reason":"missing replay source"});continue
            code,created=request(f"{args.base_url}/api/build/{source}/replay","POST",{"from_pack":"itinerary-plan"})
        else:
            headers={"Idempotency-Key":f"benchmark:{args.mode}:{case['name']}"}
            if args.mode=="MOCK":headers["X-Agent-Mode"]="MOCK"
            code,created=request(f"{args.base_url}/api/build","POST",case["input"],headers)
        if code!=202:
            rows.append({"case":case["name"],"status":"request_error","http_status":code,"error":created});continue
        state=poll(args.base_url,created["job_id"],args.timeout)
        rows.append({"case":case["name"],"job_id":created["job_id"],"status":state["status"],
            "duration_ms":round((time.monotonic()-started)*1000),"progress":state.get("progress"),
            "generation_attempts":sum(x["generation_attempt_count"] for x in state.get("pack_attempts",{}).values()),
            "repair_attempts":sum(x["repair_attempt_count"] for x in state.get("pack_attempts",{}).values()),
            "token_input":sum(x.get("token_input",0) for x in state.get("pack_observability",{}).values()),
            "token_output":sum(x.get("token_output",0) for x in state.get("pack_observability",{}).values()),
            "validation_error_count":len(state.get("validation_errors",[])),"warning_count":len(state.get("validation_warnings",[])),
            "cross_day_duplicate_count":sum(x.get("code")=="CROSS_DAY_DUPLICATE" for x in state.get("validation_errors",[])),
            "route_warning_count":sum("route" in str(x).lower() or "路线" in str(x) for x in state.get("validation_warnings",[])),
            **state.get('poi_resolution_metrics',{}),**state.get('route_metrics',{})})
    completed=sum(r["status"] in ("done","done_with_warnings") for r in rows)
    report={"mode":args.mode,"created_at":datetime.now(timezone.utc).isoformat(),"case_count":len(rows),
        "metrics":{"completion_rate":completed/max(1,len(rows)),"paused_rate":sum(r["status"]=="paused" for r in rows)/max(1,len(rows)),
          "hard_failure_rate":sum(r["status"] in ("failed","request_error","timeout") for r in rows)/max(1,len(rows)),
          "average_generation_time_ms":round(sum(r.get("duration_ms",0) for r in rows)/max(1,len(rows))),
          "average_llm_calls":sum(r.get("generation_attempts",0)+r.get("repair_attempts",0) for r in rows)/max(1,len(rows)),
          "average_repair_count":sum(r.get("repair_attempts",0) for r in rows)/max(1,len(rows)),
          "token_input":sum(r.get("token_input",0) for r in rows),"token_output":sum(r.get("token_output",0) for r in rows),
          "cross_day_duplicate_count":sum(r.get("cross_day_duplicate_count",0) for r in rows),
          "invalid_reference_count":sum(r.get("validation_error_count",0) for r in rows),
          "route_warning_count":sum(r.get("route_warning_count",0) for r in rows),
          "poi_resolution_rate":sum(r.get("poi_resolution_rate",0) for r in rows)/max(1,len(rows)),
          "verified_poi_rate":sum(r.get("verified_poi_rate",0) for r in rows)/max(1,len(rows)),
          "ambiguous_poi_rate":sum(r.get("ambiguous_poi_rate",0) for r in rows)/max(1,len(rows)),
          "generated_experience_rate":sum(r.get("generated_experience_rate",0) for r in rows)/max(1,len(rows)),
          "route_success_rate":sum(r.get("route_success_rate",0) for r in rows)/max(1,len(rows)),
          "route_estimated_rate":sum(r.get("route_estimated_rate",0) for r in rows)/max(1,len(rows)),
          "schedule_feasibility_rate":sum(r.get("schedule_feasibility_rate",0) for r in rows)/max(1,len(rows)),
          "average_daily_travel_minutes":sum(r.get("avg_daily_travel_minutes",0) for r in rows)/max(1,len(rows)),
          "average_daily_stop_count":sum(r.get("avg_daily_stop_count",0) for r in rows)/max(1,len(rows)),
          "average_day_utilization":sum(r.get("avg_day_utilization",0) for r in rows)/max(1,len(rows)),
          "underfilled_day_count":sum(r.get("underfilled_day_count",0) for r in rows),
          "long_idle_gap_day_count":sum(r.get("long_idle_gap_day_count",0) for r in rows),
          "fill_added_place_count":sum(r.get("fill_added_place_count",0) for r in rows)},"cases":rows}
    output=ROOT/"benchmark_runs"/(datetime.now().strftime("%Y-%m-%d-%H%M%S")+f"-{args.mode.lower()}.json")
    output.parent.mkdir(exist_ok=True); output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"report":str(output),**report["metrics"]},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
