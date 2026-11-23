"""Read Google's reported quota reset and add a ten-minute processing margin."""
import json
from datetime import datetime, timedelta, timezone
from nbvideo import backend

def next_unlock(profile):
    usage=json.loads(backend('usage','--json',profile=profile))
    active=usage.get('active_window')
    windows=usage.get('windows') or []
    candidates=[]
    for window in windows:
        if active and window.get('kind')!=active: continue
        stamp=window.get('resets_at')
        if not stamp: continue
        reset=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if reset.tzinfo is None: reset=reset.replace(tzinfo=timezone.utc)
        unlock=reset+timedelta(minutes=10)
        if unlock>datetime.now(timezone.utc): candidates.append(unlock)
    return min(candidates).isoformat() if candidates else None
