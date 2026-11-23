#!/usr/bin/env python3
"""Install a per-user launchd check every twelve hours and at login."""
from pathlib import Path
import plistlib
import shutil
import os
import subprocess
import sys

label='it.nbvideo.drive-sync'
target=Path.home()/'Library'/'LaunchAgents'/f'{label}.plist'
# launchd must not read the scripts from ~/Documents: it may be an iCloud folder
# whose files get evicted ("Resource deadlock avoided") and macOS privacy rules
# block background agents there. Run a private copy from ~/.nbvideo/app instead.
source=Path(__file__).resolve().parent
app=Path.home()/'.nbvideo'/'app'
app.mkdir(mode=0o700,parents=True,exist_ok=True)
for module in ('nbvideo.py','sync.py','delta_sync.py','drive_upload.py','quota.py',
               'watch_until.py','continuous_queue.py','moodle_curriculum.py'):
    shutil.copy2(source/module,app/module)
program=app/'sync.py'
target.parent.mkdir(parents=True,exist_ok=True)
search_path=':'.join([str(Path.home()/'.local/bin'),'/opt/homebrew/bin','/usr/local/bin','/usr/bin','/bin'])
payload={'Label':label,'ProgramArguments':[sys.executable,str(program)],'EnvironmentVariables':{'PATH':search_path},'StartInterval':43200,'RunAtLoad':True,'StandardOutPath':str(Path.home()/'.nbvideo'/'sync.log'),'StandardErrorPath':str(Path.home()/'.nbvideo'/'sync-error.log')}
target.write_bytes(plistlib.dumps(payload))
subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(target)],capture_output=True)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)],check=True)
watch_label='it.nbvideo.queue-watch'
watch_target=target.parent/f'{watch_label}.plist'
watch_payload={'Label':watch_label,
               'ProgramArguments':[sys.executable,str(app/'watch_until.py')],
               'EnvironmentVariables':{'PATH':search_path},
               'RunAtLoad':True,
               'KeepAlive':True,
               'ThrottleInterval':60,
               'StandardOutPath':str(Path.home()/'.nbvideo'/'watch.log'),
               'StandardErrorPath':str(Path.home()/'.nbvideo'/'watch-error.log')}
watch_target.write_bytes(plistlib.dumps(watch_payload))
subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(watch_target)],capture_output=True)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(watch_target)],check=True)
print(target)
print(watch_target)
print(f'Script copiati in {app}: dopo ogni modifica rilancia schedule-install')
