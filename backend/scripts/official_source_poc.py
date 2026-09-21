"""Isolated Serper official-source PoC and Google-vs-Serper comparison."""
from __future__ import annotations
import argparse,asyncio,json,os,sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from scripts.google_places_poc import POIS,amap_place,connection
from app.services.amap import AmapClient
from app.services.official_source import OfficialSourceDiscoveryService
from app.services.official_source.providers.serper import SerperOfficialSourceProvider

# Human review is report-only evidence for this fixed PoC set. It never changes
# resolver behavior or production bindings.
MANUAL_REVIEW_BY_URL={
    'https://www.summerpalace.net.cn/':('confirmed_official','颐和园官网'),
    'https://www.shanghaimuseum.net/':('confirmed_official','上海博物馆原官方域名；搜索结果公告迁移至 .cn'),
    'https://www.shanghaidisneyresort.com/zh-cn/':('confirmed_official','上海迪士尼度假区官网'),
    'https://www.opg.cn/':('related_official','东方明珠集团网站，不是景点专用官网'),
    'https://www.yugarden.com.cn/':('confirmed_official','豫园官网'),
    'https://www.neac.gov.cn/seac/c103627/202310/1168372.shtml':('third_party','政府介绍页，不是景点官网'),
    'https://www.dha.ac.cn/skxl/mgk.htm':('confirmed_official','敦煌研究院莫高窟页面'),
    'http://www.mssyyq.com/':('confirmed_official','鸣沙山月牙泉景区官网；仅 HTTP，仍需抓取层复核'),
    'https://www.szmuseum.com/':('confirmed_official','苏州博物馆官网'),
    'https://www.panda.org.cn/':('confirmed_official','成都大熊猫繁育研究基地官网'),
}

def rate(value,total):return round(value/total,3) if total else 0
def comparison_row(report):
    metrics=report.get('metrics') or {};total=metrics.get('poc_size') or report.get('poc_size') or 0
    if report.get('execution_status')!='completed':
        return {'poc_count':total,'official_url_coverage':None,'verified_official_rate':None,
          'ambiguous_rate':None,'wrong_binding':'not_reviewed','average_latency_ms':None,
          'api_calls':None,'estimated_cost_usd':None}
    return {'poc_count':total,'candidate_url_coverage':rate(metrics.get('website_uri_count',metrics.get('selected_url_count',0)),total),
      'verified_official_rate':rate(metrics.get('manual_confirmed_official_count',metrics.get('verified_official_count',0)),total),
      'auto_verified_official_rate':rate(metrics.get('verified_official_count',0),total),
      'ambiguous_rate':rate(metrics.get('ambiguous_count',0),total),'wrong_binding':metrics.get('wrong_binding_count','not_reviewed'),
      'average_latency_ms':metrics.get('average_network_latency_ms',metrics.get('average_search_latency_ms')),
      'api_calls':metrics.get('google_network_request_count',metrics.get('query_attempt_count',metrics.get('network_query_count'))),
      'estimated_cost_usd':metrics.get('estimated_list_cost_usd')}

async def run(serper_output,comparison_output,limit):
    stamp=datetime.now(timezone.utc).isoformat()
    if not os.getenv('SERPER_API_KEY','').strip():
        report={'generated_at':stamp,'execution_status':'configuration_missing','error':'SERPER_API_KEY 未配置',
                'production_pipeline_connected':False,'poc_size':limit,'results':[]}
    else:
        connect=connection();amap=AmapClient(connect=connect);provider=SerperOfficialSourceProvider(connect=connect)
        service=OfficialSourceDiscoveryService(provider,connect=connect);results=[]
        for index,(name,city,category) in enumerate(POIS[:limit],1):
            before=len(provider.metrics)
            try:
                canonical,amap_selected=await amap_place(amap,name,city,category);canonical['id']=f'official-poc-{index:02d}'
                decision=await service.discover(canonical);request_metrics=provider.metrics[before:]
                selected=decision.selected_candidate
                results.append({'poi':name,'city':city,'amap_match':amap_selected.name if amap_selected else None,
                  'serper_query_count':len(request_metrics),'queries':[item['query'] for item in request_metrics],
                  'top_result':{'title':selected.title,'url':selected.url} if selected else None,
                  'selected_url':decision.selected_url,'resolution_confidence':decision.confidence,
                  'resolution_status':decision.status,'verified_official':decision.status in ('verified_official','cache_hit'),
                  'search_latency_ms':round(sum(item['latency_ms'] for item in request_metrics),1),
                  'cache_hit':decision.status=='cache_hit','error':None})
            except Exception as exc:
                results.append({'poi':name,'city':city,'serper_query_count':len(provider.metrics)-before,
                  'selected_url':None,'resolution_confidence':0,'resolution_status':'provider_error',
                  'verified_official':False,'search_latency_ms':0,'cache_hit':False,'error':f'{type(exc).__name__}: {exc}'})
        network=[m for m in provider.metrics if not m['cache_hit']];total=len(results)
        for item in results:
            review=MANUAL_REVIEW_BY_URL.get(item.get('selected_url'))
            item['manual_review_status']=review[0] if review else ('no_candidate' if not item.get('selected_url') else 'not_reviewed')
            item['manual_review_note']=review[1] if review else None
        with connect() as db:
            cumulative=db.execute("""SELECT COUNT(*) count,COALESCE(AVG(latency_ms),0) latency
              FROM official_search_logs WHERE place_id LIKE 'official-poc-%' AND cache_hit=0""").fetchone()
        selected=sum(bool(item.get('selected_url')) for item in results);verified=sum(item['verified_official'] for item in results)
        query_attempts=sum(item['serper_query_count'] for item in results)
        manual_verified=sum(item['manual_review_status']=='confirmed_official' for item in results)
        manual_wrong=sum(item['manual_review_status'] in ('related_official','third_party') for item in results)
        auto_verified_wrong=sum(item['verified_official'] and item['manual_review_status']!='confirmed_official' for item in results)
        metrics={'poc_size':total,'selected_url_count':selected,'verified_official_count':verified,
          'probable_official_count':sum(item['resolution_status']=='probable_official' for item in results),
          'unverified_count':sum(item['resolution_status']=='unverified' for item in results),
          'manual_confirmed_official_count':manual_verified,'manual_rejected_selected_count':manual_wrong,
          'auto_verified_wrong_binding_count':auto_verified_wrong,
          'official_source_cache_hit_count':sum(item['cache_hit'] for item in results),
          'search_response_cache_hit_count':sum(m['cache_hit'] for m in provider.metrics),
          'network_query_count':len(network),'query_attempt_count':query_attempts,'cumulative_network_query_count':cumulative['count'],
          'average_queries_per_poi':round(sum(item['serper_query_count'] for item in results)/total,3) if total else 0,
          'average_search_latency_ms':round(cumulative['latency'],1),
          'estimated_current_run_cost_usd':round(len(network)*.001,4),
          'estimated_cold_run_cost_usd':round(query_attempts*.001,4),
          'estimated_experiment_cumulative_cost_usd':round(cumulative['count']*.001,4),
          'estimated_list_cost_usd':round(query_attempts*.001,4),'wrong_binding_count':manual_wrong}
        report={'generated_at':stamp,'execution_status':'completed','production_pipeline_connected':False,
          'provider':'serper','configuration':{'max_results':provider.max_results,'max_queries_per_poi':2,
          'cache_ttl_seconds':service.ttl,'google_fallback_enabled':False},
          'billing_note':'Starter 标价约 $1/1000 次成功查询；新账户免费查询额度、税费和实际套餐另计。',
          'metrics':metrics,'results':results}
    serper_output.parent.mkdir(parents=True,exist_ok=True);serper_output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    google_path=ROOT/'benchmark_runs/google_places_poc.json'
    google=json.loads(google_path.read_text(encoding='utf-8')) if google_path.exists() else {'execution_status':'not_run'}
    comparison={'generated_at':stamp,'google_execution_status':google.get('execution_status'),
      'serper_execution_status':report.get('execution_status'),'providers':{'google_places':comparison_row(google),'serper':comparison_row(report)},
      'recommendation':'insufficient_data' if report.get('execution_status')!='completed' else (
        'serper_viable_as_discovery_primary_with_strict_resolution_and_fetch_verification'
        if report['metrics'].get('auto_verified_wrong_binding_count')==0 else 'resolver_not_safe_for_primary_use')}
    comparison_output.write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'serper_report':str(serper_output),'comparison_report':str(comparison_output),
                      'execution_status':report.get('execution_status'),'metrics':report.get('metrics')},ensure_ascii=False,indent=2))
    return 0 if report.get('execution_status')=='completed' else 2

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--output',type=Path,default=ROOT/'benchmark_runs/serper_official_source_poc.json')
    parser.add_argument('--comparison-output',type=Path,default=ROOT/'benchmark_runs/google_vs_serper_official_source.json')
    args=parser.parse_args();raise SystemExit(asyncio.run(run(args.output,args.comparison_output,max(1,min(args.limit,20)))))
