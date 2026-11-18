#!/usr/bin/env python3
"""Summarize the sampler output without attributing shared-shell costs to a plugin."""
import argparse
import json
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('folder',type=Path)
a=p.parse_args()
rows=[json.loads(x) for x in (a.folder/'samples.jsonl').read_text().splitlines()]
summary={'start_utc':rows[0]['time_utc'],'end_utc':rows[-1]['time_utc'],
         'sample_count':len(rows),'duration_seconds':rows[-1]['elapsed_seconds']-rows[0]['elapsed_seconds'],
         'intervals_seconds':[round(b['elapsed_seconds']-a['elapsed_seconds'],3) for a,b in zip(rows,rows[1:])],
         'shell_processes':[], 'collector_runs_seen':[],
         'initial_state_file_bytes':rows[0]['state_file_bytes'],'final_state_file_bytes':rows[-1]['state_file_bytes'],
         'limitations':['Quickshell hosts all Omarchy plugins: its CPU and memory cannot be attributed to Drive Health.',
                       '30-second snapshots miss short-lived report-reader processes; collector accounting retains its last-run usage.',
                       'This observation window can detect obvious churn, but cannot rule out long-term leaks.',
                       'CPU percentages use one logical core as 100%; child CPU includes all reaped shell helpers.']}
keys={(s['pid'],s['start_ticks']) for r in rows for s in r['shells']}
for pid,start in sorted(keys):
    series=[(r['elapsed_seconds'],s) for r in rows for s in r['shells'] if (s['pid'],s['start_ticks'])==(pid,start)]
    first,last=series[0][1],series[-1][1]
    duration=series[-1][0]-series[0][0]
    cpu=last['cpu_seconds']-first['cpu_seconds']
    children=last['reaped_children_cpu_seconds']-first['reaped_children_cpu_seconds']
    out={'pid':pid,'observations':len(series),'cpu_seconds':round(cpu,4),'mean_cpu_pct_one_core':round(cpu/duration*100,4) if duration else None,
         'max_interval_cpu_pct_one_core':round(max(s.get('cpu_pct_one_core',0) for _,s in series),4),
         'reaped_children_cpu_seconds':round(children,4),
         'mean_reaped_children_cpu_pct_one_core':round(children/duration*100,4) if duration else None}
    for field in ['rss_bytes','pss_bytes','threads','file_descriptors']:
        values=[s[field] for _,s in series if s[field] is not None]
        out[field]={'initial':values[0],'final':values[-1],'min':min(values),'max':max(values),'change':values[-1]-values[0]} if values else None
    for field in ['read_bytes','write_bytes']:
        out[field+'_change']=last['io'].get(field,0)-first['io'].get(field,0)
    summary['shell_processes'].append(out)
seen=set()
initial=rows[0]['collector'].get('ExecMainStartTimestamp')
for r in rows:
    run=r['collector']
    key=run.get('ExecMainStartTimestamp')
    if key and key not in seen:
        seen.add(key)
        summary['collector_runs_seen'].append({'predates_window':key==initial,'first_observed_at':r['time_utc'],**run})
summary['cache_updates_during_window']=len({r['cache']['collected_at'] for r in rows if r['cache']})-1
summary['cache_errors']=[{'sample':r['index'],'errors':r['cache']['errors']} for r in rows if r['cache'] and r['cache']['errors']]
(a.folder/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
