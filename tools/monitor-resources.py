#!/usr/bin/env python3
"""Read-only host sampling. Whole-shell statistics are not plugin attribution."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--duration', type=int, default=600)
parser.add_argument('--interval', type=int, default=30)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
HZ = os.sysconf('SC_CLK_TCK')
PAGE = os.sysconf('SC_PAGE_SIZE')

def process(pid):
    base = Path('/proc') / str(pid)
    fields = (base / 'stat').read_text().rsplit(')', 1)[1].split()
    io = {}
    try:
        io = {k: int(v) for k,v in (line.split(':') for line in (base / 'io').read_text().splitlines())}
    except (OSError, ValueError):
        pass
    pss = None
    try:
        for line in (base / 'smaps_rollup').read_text().splitlines():
            if line.startswith('Pss:'):
                pss = int(line.split()[1]) * 1024
    except OSError:
        pass
    return dict(pid=pid, start_ticks=int(fields[19]), cpu_seconds=(int(fields[11])+int(fields[12]))/HZ,
                reaped_children_cpu_seconds=(int(fields[13])+int(fields[14]))/HZ,
                rss_bytes=int(fields[21])*PAGE, pss_bytes=pss, threads=int(fields[17]),
                file_descriptors=len(list((base/'fd').iterdir())), io=io)

def shell_pids():
    found=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try:
            if (path/'comm').read_text().strip() == 'quickshell':
                command=(path/'cmdline').read_bytes().split(b'\0')
                # Record only executable/config arguments, never other apps' arguments.
                found.append((int(path.name), [x.decode(errors='replace') for x in command if x]))
        except OSError:
            pass
    return found

properties=['Result','ActiveState','CPUUsageNSec','MemoryPeak','MemoryCurrent','IOReadBytes','IOWriteBytes',
            'ExecMainStartTimestamp','ExecMainExitTimestamp','ExecMainStatus']
previous={}
start=time.monotonic()
with (args.output/'samples.jsonl').open('w') as log:
    for index in range(args.duration//args.interval+1):
        deadline=start+index*args.interval
        time.sleep(max(0,deadline-time.monotonic()))
        now=time.monotonic()
        shells=[]
        for pid, command in shell_pids():
            try:
                current=process(pid)
            except (OSError,ValueError,IndexError):
                continue
            current['command']=command
            key=(pid,current['start_ticks'])
            old=previous.get(key)
            if old:
                elapsed=now-old[0]
                current['cpu_pct_one_core']=100*(current['cpu_seconds']-old[1]['cpu_seconds'])/elapsed
                current['reaped_children_cpu_pct_one_core']=100*(current['reaped_children_cpu_seconds']-old[1]['reaped_children_cpu_seconds'])/elapsed
                current['read_bytes_delta']=current['io'].get('read_bytes',0)-old[1]['io'].get('read_bytes',0)
                current['write_bytes_delta']=current['io'].get('write_bytes',0)-old[1]['io'].get('write_bytes',0)
            previous[key]=(now,current)
            shells.append(current)
        result=subprocess.run(['systemctl','show','omarchy-drive-health.service',*[f'--property={p}' for p in properties]],capture_output=True,text=True,timeout=5)
        service=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        cache=Path('/var/lib/omarchy-drive-health/report.json')
        try:
            report=json.loads(cache.read_text())
            cache_state={'collected_at':report.get('collected_at'),'drive_count':len(report.get('drives',[])), 'errors':report.get('errors',[]),'bytes':cache.stat().st_size}
        except (OSError,ValueError):
            cache_state=None
        sizes={}
        for name in ['history.sqlite3','report.json']:
            try: sizes[name]=(cache.parent/name).stat().st_size
            except OSError: pass
        row={'index':index,'time_utc':dt.datetime.now(dt.timezone.utc).isoformat(), 'elapsed_seconds':round(now-start,3),
             'shells':shells,'collector':service,'cache':cache_state,'state_file_bytes':sizes,
             'service_query_error':result.stderr.strip() if result.returncode else None}
        log.write(json.dumps(row)+'\n'); log.flush()
        print(json.dumps({'sample':index,'elapsed':row['elapsed_seconds'],'shells':[{'pid':s['pid'],'cpu_pct':round(s.get('cpu_pct_one_core',0),3),'rss_mib':round(s['rss_bytes']/2**20,2),'pss_mib':round(s['pss_bytes']/2**20,2) if s['pss_bytes'] else None} for s in shells], 'collector_started':service.get('ExecMainStartTimestamp')}),flush=True)
print('COMPLETE '+str(args.output/'samples.jsonl'),flush=True)
