#!/usr/bin/env python3
"""Keep the video queue supplied and follow Google's quota unlocks."""
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import time as clock
from zoneinfo import ZoneInfo

from nbvideo import load
from quota import next_unlock

ROME=ZoneInfo('Europe/Rome')

def attempt(*args):
    try:
        subprocess.run([sys.executable,str(Path(__file__).with_name(args[0])),*args[1:]],check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f'Controllo incompleto: {exc}',file=sys.stderr,flush=True)
        return False

def deadline():
    """Next 08:00 in Rome: today if it is still night, otherwise tomorrow."""
    now=datetime.now(ROME)
    end=datetime.combine(now.date(),time(8,0),ROME)
    return end if now<end else datetime.combine(now.date()+timedelta(days=1),time(8,0),ROME)

def main():
    end=deadline()
    print(f'Controllo attivo fino a {end.isoformat()}',flush=True)
    while datetime.now(ROME)<end:
        state=load()
        if not state.get('accounts'): raise RuntimeError('Nessun account configurato')
        attempt('nbvideo.py','notebook-discover')
        tool='sync.py' if state.get('drive_remote') else 'delta_sync.py'
        attempt(tool)
        attempt('continuous_queue.py')
        attempt('nbvideo.py','run')
        attempt(tool)
        # A generation confirmed missing in two scans can be resubmitted once.
        retryable=[job['id'] for job in load()['jobs']
                   if job.get('exam')=='Chimica Inorganica I — Morpurgo'
                   and job.get('retry_count',0)<4
                   and ((job['status']=='failed'
                         and 'Video non trovato in due scansioni' in job.get('error',''))
                        or (job['status']=='needs_attention'
                            and not job.get('task_id')
                            and 'nodename nor servname provided' in job.get('error','')))]
        if retryable:
            attempt('nbvideo.py','queue-retry',*retryable)
        attempt('continuous_queue.py')
        attempt('nbvideo.py','run')
        state=load()  # run/sync have updated retry_at and statuses
        unlocks=[]
        for account in state['accounts']:
            try:
                stamp=next_unlock(account)
                if stamp: unlocks.append(datetime.fromisoformat(stamp).astimezone(ROME))
            except (OSError,ValueError,subprocess.CalledProcessError) as exc:
                print(f'{account}: scadenza non disponibile: {exc}',file=sys.stderr,flush=True)
        for info in state['accounts'].values():
            if info.get('blocked_until'):
                unlocks.append(datetime.fromisoformat(info['blocked_until']).astimezone(ROME))
        for job in state['jobs']:
            if job.get('status')=='pending' and job.get('retry_at'):
                unlocks.append(datetime.fromisoformat(job['retry_at']).astimezone(ROME))
        future=[dt for dt in unlocks if dt>datetime.now(ROME)]
        # A missing reset time must not leave pending work asleep until tomorrow.
        fallback=datetime.now(ROME)+timedelta(hours=6 if any(j['status']=='pending' for j in state['jobs']) else 12)
        next_at=min(future+[end,fallback])
        print(f'Prossimo controllo: {next_at.isoformat()}',flush=True)
        while datetime.now(ROME)<next_at:
            clock.sleep(min(60,max(1,(next_at-datetime.now(ROME)).total_seconds())))
    print('Controllo notturno concluso',flush=True)

if __name__=='__main__':
    try: main()
    except (OSError,RuntimeError) as exc: print(exc,file=sys.stderr); sys.exit(1)
