#!/usr/bin/env python3
"""Reconcile NotebookLM videos with local files and Google Drive.

Completed videos are downloaded and uploaded once; failed generations mark
their job 'failed'. Every state change goes through nbvideo.update (locked),
so this can run while agents queue work or run submits it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from nbvideo import ROOT, load, update, now
from delta_sync import cli, records, artifact_id, is_complete
from drive_upload import folder_name

FAILED_MESSAGE = 'Generazione fallita su NotebookLM: usa queue-retry per riprovare'
DRIVE_ROOT = 'Gemini Notebook Video'
RCLONE_UPLOAD_TIMEOUT = 180
# Cartella Drive: Google Drive for desktop on the Mac, or the same folder mounted in an agent VM.
_DRIVE_CANDIDATES = [
    os.environ.get('NBVIDEO_DRIVE_DIR'),
    str(Path.home() / 'Library/CloudStorage/GoogleDrive-<IL-TUO-ACCOUNT>/Il mio Drive/Gemini Notebook Video'),
]
MOUNTED_DRIVE = next((Path(c) for c in _DRIVE_CANDIDATES if c and Path(c).is_dir()), Path(_DRIVE_CANDIDATES[1]))


def upload_to_drive(local, remote_path):
    subprocess.run([
        'rclone', 'copyto', str(local), remote_path, '--checksum',
        '--contimeout', '20s', '--timeout', '60s', '--retries', '2',
        '--low-level-retries', '2',
    ], check=True, timeout=RCLONE_UPLOAD_TIMEOUT)


def upload_to_mounted_drive(local, drive_path):
    relative = Path(drive_path).relative_to(DRIVE_ROOT)
    destination = MOUNTED_DRIVE / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == Path(local).stat().st_size:
        return
    fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        shutil.copy2(local, temporary)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_failed(row):
    return str(row.get('status', row.get('state', ''))).strip().lower() in {'failed', 'error'}


def matching_jobs(state, account, aid):
    return [job for job in state['jobs'] if job.get('account') == account and job.get('task_id') == aid]


def mark_failed(state, account, aid):
    changed = False
    for job in matching_jobs(state, account, aid):
        if job['status'] == 'submitted':
            job['status'] = 'failed'
            job['error'] = FAILED_MESSAGE
            changed = True
    return changed


VANISHED_MESSAGE = 'Video non trovato in due scansioni distanti almeno 10 minuti: usa queue-retry per riprovare'
VANISH_GRACE = timedelta(minutes=10)


def mark_vanished(state, account, nbid, present):
    """Require two missing scans before treating a submitted video as failed."""
    changed = False
    for job in state['jobs']:
        if (job['status'] != 'submitted' or job.get('account') != account or job.get('notebook_id') != nbid
                or not job.get('task_id') or not job.get('submitted_at')):
            continue
        if job['task_id'] in present:
            job.pop('missing_since', None)
            continue
        if now() - datetime.fromisoformat(job['submitted_at']) < VANISH_GRACE:
            continue
        if not job.get('missing_since'):
            job['missing_since'] = now().isoformat()
            continue
        if now() - datetime.fromisoformat(job['missing_since']) < VANISH_GRACE:
            continue
        job['status'] = 'failed'
        job['error'] = VANISHED_MESSAGE
        job.pop('missing_since', None)
        changed = True
    return changed


def record_upload(state, key, entry, account, aid):
    for job in matching_jobs(state, account, aid):
        job['status'] = 'uploaded'
        job['uploaded_at'] = entry['uploaded_at']
        for field in ('exam', 'level', 'topic', 'parent', 'format', 'meta'):
            if job.get(field) not in (None, '', {}):
                entry[field] = job[field]
    state.setdefault('uploaded_artifacts', {})[key] = entry


def manifest(state):
    """Feed index: ordered by exam, then depth level, then upload time."""
    videos = list(state.get('uploaded_artifacts', {}).values())
    videos.sort(key=lambda v: (str(v.get('exam') or ''), v.get('level') if v.get('level') is not None else 99,
                               v.get('uploaded_at', '')))
    return {'generated_at': now().isoformat(), 'videos': videos}


def main():
    state = load()
    remote = state.get('drive_remote')
    if not remote:
        raise RuntimeError('Configura il Drive: nbvideo.py drive-set-remote NOME')
    mounted_drive = MOUNTED_DRIVE if remote == 'sapienza' and MOUNTED_DRIVE.is_dir() else None
    failures = 0
    for account, info in state['accounts'].items():
        for notebook, nbid in info.get('notebooks', {}).items():
            try:
                items = records(json.loads(cli(account, 'artifact', 'list', '--type', 'video', '--json', '-n', nbid)))
            except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                print(f'{account}/{notebook}: elenco video fallito: {exc}', file=sys.stderr)
                failures += 1
                continue
            present = {artifact_id(item) for item in items}
            if update(lambda s, a=account, n=nbid, p=present: mark_vanished(s, a, n, p)):
                print(f'{account}/{notebook}: video spariti da NotebookLM, marcati failed', file=sys.stderr)
            for item in items:
                aid = artifact_id(item)
                if not aid:
                    continue
                if is_failed(item):
                    if update(lambda s, a=account, i=aid: mark_failed(s, a, i)):
                        print(f'{account}/{notebook}/{aid}: generazione fallita', file=sys.stderr)
                    continue
                if not is_complete(item):
                    continue
                key = f'{account}:{nbid}:{aid}'
                already_uploaded = key in load().get('uploaded_artifacts', {})
                local = ROOT / 'downloads' / folder_name(account) / folder_name(notebook) / f'{aid}.mp4'
                filename = f'{folder_name(account)}--{aid}.mp4'
                drive_path = f'{DRIVE_ROOT}/{folder_name(notebook)}/{filename}'
                if already_uploaded:
                    if mounted_drive:
                        try:
                            local.parent.mkdir(parents=True, exist_ok=True)
                            if not local.is_file() or local.stat().st_size == 0:
                                cli(account, 'download', 'video', str(local), '--notebook', nbid, '--artifact', aid, '--no-clobber')
                            if not local.is_file() or local.stat().st_size == 0:
                                raise RuntimeError('MP4 assente o vuoto')
                            upload_to_mounted_drive(local, drive_path)
                        except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                            print(f'{account}/{notebook}/{aid}: {exc}', file=sys.stderr)
                            failures += 1
                    continue
                try:
                    local.parent.mkdir(parents=True, exist_ok=True)
                    if not local.is_file() or local.stat().st_size == 0:
                        cli(account, 'download', 'video', str(local), '--notebook', nbid, '--artifact', aid, '--no-clobber')
                    if not local.is_file() or local.stat().st_size == 0:
                        raise RuntimeError('MP4 assente o vuoto')
                    if mounted_drive:
                        upload_to_mounted_drive(local, drive_path)
                    else:
                        upload_to_drive(local, f'{remote}:{drive_path}')
                    entry = {'notebook': notebook, 'account': account, 'artifact_id': aid,
                             'drive_path': drive_path, 'uploaded_at': now().isoformat()}
                    update(lambda s, k=key, e=entry, a=account, i=aid: record_upload(s, k, e, a, i))
                    print(f'Caricato {remote}:{drive_path}')
                except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    print(f'{account}/{notebook}/{aid}: {exc}', file=sys.stderr)
                    failures += 1
    latest = load()
    if latest.get('uploaded_artifacts'):
        index = ROOT / 'downloads' / 'index.json'
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps(manifest(latest), ensure_ascii=False, indent=2))
        try:
            if mounted_drive:
                upload_to_mounted_drive(index, f'{DRIVE_ROOT}/index.json')
            else:
                upload_to_drive(index, f'{remote}:{DRIVE_ROOT}/index.json')
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f'Upload indice fallito: {exc}', file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
