#!/usr/bin/env python3
"""nbvideo: coda di Video Overview NotebookLM su più account Google.

Pensata per essere pilotata da agenti (Codex, Claude): ogni comando che
modifica la coda prende un lock su state.json, quindi più agenti e
watch_until.py possono lavorare insieme senza perdere scritture.
Vedi AGENTS.md per il protocollo d'uso.
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / '.nbvideo'
STATE = ROOT / 'state.json'
LOCK = ROOT / 'state.lock'
MAX_ACCOUNTS = 4
FORMATS = ['explainer', 'brief', 'short', 'cinematic']
RETRYABLE = ('needs_attention', 'failed')
# Jobs in these states already cover a topic: a new identical request is a duplicate.
COVERING = ('pending', 'submitting', 'submitted', 'uploaded')
STATUSES = ('pending', 'submitting', 'submitted', 'uploaded', 'failed', 'needs_attention', 'cancelled')


class MissingBackend(Exception):
    pass


# ---------------------------------------------------------------- state

def now():
    return datetime.now(timezone.utc)


def load():
    try:
        state = json.loads(STATE.read_text())
    except FileNotFoundError:
        state = {}
    state.setdefault('accounts', {})
    state.setdefault('jobs', [])
    return state


def save(state):
    ROOT.mkdir(mode=0o700, exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.chmod(temporary, 0o600)
    temporary.replace(STATE)


_threads = threading.Lock()


@contextmanager
def locked():
    """Exclusive read-modify-write of state.json across processes and threads.

    Never nest it, and never call the NotebookLM backend while holding it.
    """
    ROOT.mkdir(mode=0o700, exist_ok=True)
    with _threads, open(LOCK, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            state = load()
            yield state
            save(state)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def update(change):
    with locked() as state:
        return change(state)


def find(state, job_id):
    return next(job for job in state['jobs'] if job['id'] == job_id)


def is_future(stamp):
    return bool(stamp) and datetime.fromisoformat(stamp) > now()


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- backend

def backend(*args, profile=None, interactive=False):
    executable = shutil.which('notebooklm') or str(Path.home() / '.local/bin/notebooklm')
    if not Path(executable).exists():
        raise MissingBackend()
    command = [executable] + (['--profile', profile] if profile else []) + list(args)
    return subprocess.run(command, check=True, capture_output=not interactive, text=True).stdout


def failure_payload(error):
    try:
        payload = json.loads(error.stdout or error.stderr or '{}')
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def last_line(text):
    lines = (text or '').strip().splitlines()
    return lines[-1] if lines else ''


# ---------------------------------------------------------------- run

_print = threading.Lock()


def say(message, error=False):
    with _print:
        print(message, file=sys.stderr if error else sys.stdout, flush=True)


def shorts_only():
    """The presence of the marker pauses long-form dispatch as well as refill."""
    return (ROOT / 'morpurgo-curated-shorts').exists()


def curated_prompt(item):
    instruction = ('Short verticale 9:16 per telefono, 45-60 secondi, un solo concetto. '
                   'Basati sulla dispensa Moodle indicata e spiega formule e passaggi nel modo usato dal professore. '
                   'Scritte grandi, un grafico o schema leggibile, inizio immediato senza saluti, una conclusione breve. '
                   'Non aggiungere formule assenti dalla fonte.')
    if str(item).startswith(instruction):
        return item
    return f"{instruction}\n\n{item}"


def eligible(job, account, info):
    return (not (shorts_only() and job.get('exam') == 'Chimica Inorganica I — Morpurgo'
                 and job.get('format') != 'short')
            and job['status'] == 'pending'
            and job.get('account') in (None, account)
            and job['notebook'] in info['notebooks']
            and not is_future(job.get('retry_at')))


def claim(account):
    """Atomically take the next eligible job this account can produce."""
    def take(state):
        info = state['accounts'].get(account)
        if not info or is_future(info.get('blocked_until')):
            return None
        for job in state['jobs']:
            if eligible(job, account, info):
                if shorts_only() and job.get('exam') == 'Chimica Inorganica I — Morpurgo':
                    job['prompt'] = curated_prompt(job['prompt'])
                job.update(status='submitting', account=account,
                           notebook_id=info['notebooks'][job['notebook']],
                           claimed_by=os.getpid(), claimed_at=now().isoformat())
                return dict(job)
        return None
    return update(take)

def release(state, job_id, status, **fields):
    """Move a job to a new status; unpinned jobs going back to pending lose their account."""
    job = find(state, job_id)
    job['status'] = status
    for key in ('claimed_by', 'claimed_at'):
        job.pop(key, None)
    if status == 'pending' and not job.get('pinned'):
        job['account'] = None
        job.pop('notebook_id', None)
    job.update(fields)


def worker(account, totals, limit):
    """Submit jobs for one account until it runs out of work, quota or --max."""
    while True:
        with _print:
            if limit and totals['attempted'] >= limit:
                return
            totals['attempted'] += 1
        job = claim(account)
        if not job:
            with _print:
                totals['attempted'] -= 1
            return
        label = job.get('topic') or job['id']
        output = ''
        try:
            output = backend('generate', 'video', job['prompt'], '--notebook', job['notebook_id'],
                             '--format', job['format'], '--language', job['language'], '--json',
                             profile=account)
            result = json.loads(output)
        except subprocess.CalledProcessError as error:
            payload = failure_payload(error)
            if payload.get('code') == 'RATE_LIMITED':
                from quota import next_unlock
                try:
                    until = next_unlock(account)
                except (ValueError, OSError, subprocess.CalledProcessError):
                    until = None

                def limited(state):
                    release(state, job['id'], 'pending')
                    state['accounts'][account]['blocked_until'] = until
                    state['accounts'][account]['blocked_note'] = (
                        None if until else 'quota esaurita: Google non ha indicato quando si sblocca')
                update(limited)
                with _print:
                    totals['paused'].append(account)
                say(f'{account}: quota esaurita, ' + (f'riprende dopo {until}' if until else 'scadenza non fornita da Google'))
            else:
                detail = payload.get('message') or last_line(error.stderr) or str(error)
                update(lambda state: release(state, job['id'], 'needs_attention', error=detail))
                with _print:
                    totals['errors'].append(job['id'])
                say(f'{account}: errore su "{label}": {detail}', error=True)
            return  # stop this account for this run; the other accounts keep going
        except ValueError:
            update(lambda state: release(state, job['id'], 'needs_attention',
                                         error=f'Risposta non JSON: {output[:200]}'))
            with _print:
                totals['errors'].append(job['id'])
            say(f'{account}: risposta inattesa su "{label}"', error=True)
            return

        def submitted(state):
            release(state, job['id'], 'submitted', task_id=result.get('task_id'),
                    submitted_at=now().isoformat())
            for key in ('error', 'retry_at'):
                find(state, job['id']).pop(key, None)
            state['accounts'][account].pop('blocked_until', None)
            state['accounts'][account].pop('blocked_note', None)
        update(submitted)
        with _print:
            totals['submitted'] += 1
        say(f'{account}: inviato "{label}"')


def recover_interrupted():
    """A job left in 'submitting' by a dead process may or may not exist on Google."""
    def fix(state):
        for job in state['jobs']:
            if job['status'] == 'submitting' and not alive(job.get('claimed_by')):
                release(state, job['id'], 'needs_attention',
                        error='Invio interrotto: controlla il quaderno prima di riprovare per evitare duplicati')
    update(fix)


def run(limit, as_json):
    recover_interrupted()
    accounts = [name for name, info in load()['accounts'].items() if info.get('notebooks')]
    totals = {'submitted': 0, 'attempted': 0, 'paused': [], 'errors': []}
    threads = [threading.Thread(target=worker, args=(name, totals, limit)) for name in accounts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    totals['pending'] = sum(1 for job in load()['jobs'] if job['status'] == 'pending')
    del totals['attempted']
    if as_json:
        print(json.dumps(totals, ensure_ascii=False))
    else:
        print(f"Inviati {totals['submitted']}; in coda {totals['pending']}; "
              f"account in pausa {len(totals['paused'])}; errori {len(totals['errors'])}")


# ---------------------------------------------------------------- queue

def read_plan(path, defaults):
    """Topics from a text file (one per line) or JSONL (one object per line)."""
    if not path.is_file():
        raise SystemExit(f'File argomenti inesistente: {path}')
    items = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if path.suffix == '.jsonl':
            try:
                item = json.loads(line)
            except ValueError as error:
                raise SystemExit(f'{path}:{number}: JSON non valido: {error}')
            if not isinstance(item, dict) or not str(item.get('topic', '')).strip():
                raise SystemExit(f'{path}:{number}: serve un oggetto con "topic"')
            items.append({**defaults, **item})
        else:
            items.append({**defaults, 'topic': line})
    return items


def topic_key(notebook, exam, level, topic):
    return (notebook, (exam or '').strip().lower(), level, ' '.join(str(topic).lower().split()))


def parent_ready(state, notebook, item):
    """Depth rule: a level-N topic (N>1) needs its parent uploaded at level N-1."""
    level = item.get('level')
    if level is None or level <= 1:
        return True
    parent = item.get('parent')
    if not parent:
        return False
    want = topic_key(notebook, item.get('exam'), level - 1, parent)
    return any(job['status'] == 'uploaded'
               and topic_key(job['notebook'], job.get('exam'), job.get('level'), job['topic']) == want
               for job in state['jobs'])


def load_syllabus(path):
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise SystemExit(f'{path}:{number}: JSON non valido: {error}')
        if not isinstance(row, dict) or not str(row.get('topic', '')).strip() or not isinstance(row.get('level'), int):
            raise SystemExit(f'{path}:{number}: servono "topic" e "level" intero')
        if row['level'] > 1 and not row.get('parent'):
            raise SystemExit(f'{path}:{number}: dal livello 2 serve "parent"')
        rows.append({k: row[k] for k in ('topic', 'level', 'parent', 'prompt', 'format') if row.get(k) is not None})
    topics = {(r['level'], r['topic']) for r in rows}
    for r in rows:
        if r['level'] > 1 and (r['level'] - 1, r['parent']) not in topics:
            raise SystemExit(f"Syllabus: padre {r['parent']!r} di {r['topic']!r} assente al livello {r['level'] - 1}")
    return rows


def next_topics(state, exam, notebook, limit):
    """Syllabus topics not yet covered whose parent is consolidated, shallowest first."""
    # Failed and attention jobs must be retried, never recreated as duplicates.
    covered = {topic_key(job['notebook'], job.get('exam'), job.get('level'), job['topic'])
               for job in state['jobs'] if job['status'] != 'cancelled' and job.get('topic')}
    ready = [dict(row, exam=exam) for row in state.get('syllabi', {}).get(exam, [])
             if topic_key(notebook, exam, row['level'], row['topic']) not in covered
             and parent_ready(state, notebook, dict(row, exam=exam))]
    ready.sort(key=lambda row: row['level'])
    return ready[:limit] if limit else ready


def queue_add(x, parser):
    if not 1 <= x.count <= 100:
        parser.error('--count deve essere tra 1 e 100')
    meta = {}
    if x.meta:
        try:
            meta = json.loads(x.meta)
        except ValueError:
            parser.error('--meta deve essere JSON')
    defaults = {'prompt': x.prompt, 'format': x.format, 'language': x.language, 'exam': x.exam,
                'level': x.level, 'parent': x.parent, 'meta': meta}
    items = [{**defaults, 'topic': topic} for topic in x.topic]
    if x.topics_file:
        items += read_plan(x.topics_file, defaults)
    if shorts_only() and x.exam == 'Chimica Inorganica I — Morpurgo':
        for item in items:
            item['format'] = 'short'
            item['prompt'] = curated_prompt(item.get('prompt') or item.get('topic') or '')
    if not items:
        items = [{**defaults, 'topic': ''}]
    for item in items:
        if item.get('format') not in FORMATS:
            parser.error(f"Formato non valido per {item.get('topic')!r}: {item.get('format')}")
        if item.get('level') is not None and not isinstance(item['level'], int):
            parser.error(f"level deve essere un intero ({item.get('topic')!r})")

    def add(state):
        owners = [name for name, info in state['accounts'].items() if x.notebook in info['notebooks']]
        if not owners:
            parser.error(f'Quaderno sconosciuto: {x.notebook} (usa notebook-list)')
        if x.account and x.account not in owners:
            parser.error(f'{x.account} non ha il quaderno {x.notebook}')
        if not x.force_depth:
            blocked = [f"{item['topic']} (L{item['level']}, padre {item.get('parent')!r})" for item in items
                       if not parent_ready(state, x.notebook, item)]
            if blocked:
                parser.error('Livello precedente non ancora consolidato (uploaded) per: ' + '; '.join(blocked)
                             + '. Produci prima il padre, oppure usa --force-depth')
        existing = {topic_key(job['notebook'], job.get('exam'), job.get('level'), job['topic'])
                    for job in state['jobs'] if job['status'] in COVERING and job.get('topic')}
        added, skipped = [], []
        for item in items:
            topic = str(item['topic']).strip()
            key = topic_key(x.notebook, item.get('exam'), item.get('level'), topic)
            if topic and key in existing and not x.allow_duplicates:
                skipped.append(topic)
                continue
            existing.add(key)
            prompt = ' '.join(part for part in (
                f"Corso universitario: {item['exam']}." if item.get('exam') else '',
                f"Approfondisci il tema già trattato «{item['parent']}»." if item.get('parent') else '',
                item.get('prompt') or '',
                f'Concentrati su questo argomento: {topic}.' if topic else '') if part)
            for _ in range(x.count):
                job = {'id': str(uuid.uuid4()), 'account': x.account, 'pinned': bool(x.account),
                       'notebook': x.notebook, 'topic': topic, 'prompt': prompt,
                       'format': item['format'], 'language': item.get('language') or 'it',
                       'exam': item.get('exam'), 'level': item.get('level'),
                       'parent': item.get('parent'), 'meta': item.get('meta') or {},
                       'status': 'pending', 'created_at': now().isoformat()}
                state['jobs'].append(job)
                added.append(job['id'])
        return added, skipped
    added, skipped = update(add)
    if x.json:
        print(json.dumps({'added': added, 'skipped_duplicates': skipped}, ensure_ascii=False))
    else:
        print(f'{len(added)} richieste in coda' + (f'; {len(skipped)} già coperte, saltate' if skipped else ''))


def select_jobs(state, statuses=None, exam=None, notebook=None):
    return [job for job in state['jobs']
            if (not statuses or job['status'] in statuses)
            and (exam is None or job.get('exam') == exam)
            and (notebook is None or job['notebook'] == notebook)]


def job_detail(job):
    if job['status'] == 'pending' and job.get('retry_at'):
        return f"riprova dopo {job['retry_at']}"
    return last_line(job.get('error'))


def source_key(source):
    return source if '://' in source else Path(source).name


def source_key_entry(entry):
    account, nbid, source = entry.split(':', 2)
    return f'{account}:{nbid}:{source_key(source)}'


def missing_login(account):
    """Profile without storage_state.json: the account cannot submit until it logs in."""
    home = Path(os.environ.get('NOTEBOOKLM_HOME') or Path.home() / '.notebooklm')
    if (home / 'profiles' / account / 'storage_state.json').is_file():
        return None
    return f'login mancante: notebooklm -p {account} login --browser chrome'


def status_summary(state):
    accounts = {}
    for name, info in state['accounts'].items():
        accounts[name] = {
            'notebooks': len(info.get('notebooks', {})),
            'paused_until': info.get('blocked_until') if is_future(info.get('blocked_until')) else None,
            'note': info.get('blocked_note') or missing_login(name),
            'in_production': sum(1 for job in state['jobs']
                                 if job.get('account') == name and job['status'] == 'submitted'),
        }
    counts, by_exam = {}, {}
    for job in state['jobs']:
        counts[job['status']] = counts.get(job['status'], 0) + 1
        exam = job.get('exam') or '-'
        level = str(job['level']) if job.get('level') is not None else '-'
        bucket = by_exam.setdefault(exam, {}).setdefault(level, {})
        bucket[job['status']] = bucket.get(job['status'], 0) + 1
    attention = [{'id': job['id'], 'status': job['status'], 'account': job.get('account'),
                  'topic': job.get('topic'), 'detail': job_detail(job)}
                 for job in state['jobs'] if job['status'] in RETRYABLE][:10]
    return {'accounts': accounts, 'jobs': counts, 'by_exam_level': by_exam,
            'attention': attention, 'drive_remote': state.get('drive_remote')}


def coverage(state, exam, notebook):
    topics = {}
    for job in select_jobs(state, exam=exam, notebook=notebook):
        if not job.get('topic') or job['status'] == 'cancelled':
            continue
        key = (job.get('exam'), job.get('level'), job['topic'])
        row = topics.setdefault(key, {'exam': job.get('exam'), 'level': job.get('level'),
                                      'topic': job['topic'], 'parent': job.get('parent'),
                                      'notebook': job['notebook']})
        row[job['status']] = row.get(job['status'], 0) + 1
    return sorted(topics.values(), key=lambda row: (str(row['exam']), row['level'] or 0, row['topic']))


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(prog='nbvideo', description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('account-add', help='crea un profilo e fa il login Google')
    a.add_argument('name')
    a.add_argument('--browser', choices=['chromium', 'chrome', 'msedge'],
                   help="usa 'chrome' se Chromium si chiude su macOS 15+")
    sub.add_parser('account-list')
    imported = sub.add_parser('account-import', help='registra un profilo notebooklm già esistente')
    imported.add_argument('profile')

    sub.add_parser('notebook-discover', help='registra tutti i quaderni di tutti gli account')
    n = sub.add_parser('notebook-add')
    n.add_argument('name'); n.add_argument('id'); n.add_argument('--account', required=True)
    sub.add_parser('notebook-list')
    c = sub.add_parser('notebook-create', help='crea lo stesso quaderno su tutti gli account')
    c.add_argument('name'); c.add_argument('sources', nargs='+')
    add = sub.add_parser('source-add', help='aggiunge fonti al quaderno su tutti gli account')
    add.add_argument('notebook'); add.add_argument('sources', nargs='+')

    q = sub.add_parser('queue-add', help='accoda richieste video (gli argomenti già coperti vengono saltati)')
    q.add_argument('--notebook', required=True)
    q.add_argument('--topic', action='append', default=[], help='argomento; ripetibile')
    q.add_argument('--topics-file', type=Path, help='un argomento per riga, oppure .jsonl con un oggetto per riga')
    q.add_argument('--prompt', default='')
    q.add_argument('--count', type=int, default=1, help='video per argomento')
    q.add_argument('--format', choices=FORMATS, default='explainer')
    q.add_argument('--language', default='it')
    q.add_argument('--exam', help='esame/corso a cui appartiene')
    q.add_argument('--level', type=int, help='profondità: 1 = concetto base, poi sempre più nel dettaglio')
    q.add_argument('--parent', help='argomento padre già consolidato')
    q.add_argument('--meta', help="JSON libero per l'agente")
    q.add_argument('--account', help='forza un account (default: il primo account libero)')
    q.add_argument('--allow-duplicates', action='store_true')
    q.add_argument('--force-depth', action='store_true', help='ignora la regola: livello N solo dopo il padre uploaded')
    q.add_argument('--json', action='store_true')

    ql = sub.add_parser('queue-list')
    ql.add_argument('--status', action='append', choices=STATUSES)
    ql.add_argument('--exam'); ql.add_argument('--notebook'); ql.add_argument('--json', action='store_true')
    md = sub.add_parser('mode', help='sceglie la modalità Morpurgo: solo Shorts o video approfonditi')
    md.add_argument('value', choices=('shorts', 'explainer'))
    r = sub.add_parser('queue-retry', help='rimette in coda failed/needs_attention')
    r.add_argument('ids', nargs='*')
    k = sub.add_parser('queue-cancel', help='annulla richieste non ancora inviate')
    k.add_argument('ids', nargs='+')

    st = sub.add_parser('status', help='riepilogo compatto per agenti')
    st.add_argument('--json', action='store_true')
    cv = sub.add_parser('coverage', help='argomenti per esame e livello, con stato dei video')
    cv.add_argument('--exam'); cv.add_argument('--notebook'); cv.add_argument('--json', action='store_true')

    sy = sub.add_parser('syllabus-set', help='registra il syllabus di un esame (.jsonl: topic, level, parent)')
    sy.add_argument('--exam', required=True); sy.add_argument('file', type=Path)
    sl = sub.add_parser('syllabus-list'); sl.add_argument('--exam')
    nx = sub.add_parser('next', help='prossimi argomenti del syllabus pronti (padre consolidato)')
    nx.add_argument('--exam', required=True); nx.add_argument('--notebook', required=True)
    nx.add_argument('--limit', type=int, default=0); nx.add_argument('--queue', action='store_true', help='accodali subito')
    nx.add_argument('--format', choices=FORMATS, default='explainer'); nx.add_argument('--prompt', default='')

    rn = sub.add_parser('run', help='invia la coda: un worker in parallelo per ogni account')
    rn.add_argument('--max', type=int, default=0, help='massimo di richieste da tentare in questo giro')
    rn.add_argument('--json', action='store_true')

    d = sub.add_parser('drive-set-remote'); d.add_argument('name')
    sub.add_parser('drive-configure'); sub.add_parser('sync'); sub.add_parser('schedule-install')
    return p


def main():
    p = build_parser()
    x = p.parse_args()

    if x.cmd == 'account-add':
        state = load()
        if x.name not in state['accounts'] and len(state['accounts']) >= MAX_ACCOUNTS:
            p.error('Massimo quattro account')
        if x.name not in state['accounts']:
            backend('profile', 'create', x.name)
        backend('login', *(['--browser', x.browser] if x.browser else []), profile=x.name, interactive=True)
        update(lambda s: s['accounts'].setdefault(x.name, {'notebooks': {}}))
    elif x.cmd == 'account-list':
        print('\n'.join(load()['accounts']) or 'Nessun account')
    elif x.cmd == 'account-import':
        def register(state):
            if x.profile not in state['accounts'] and len(state['accounts']) >= MAX_ACCOUNTS:
                p.error('Massimo quattro account')
            state['accounts'].setdefault(x.profile, {'notebooks': {}})
        update(register)
    elif x.cmd == 'notebook-discover':
        for account in list(load()['accounts']):
            try:
                data = json.loads(backend('list', '--json', profile=account))
            except subprocess.CalledProcessError as error:
                detail = failure_payload(error).get('message') or last_line(error.stderr) or str(error)
                say(f'{account}: saltato ({detail})', error=True)
                continue

            def merge(state, data=data, account=account):
                info = state['accounts'][account]
                for item in data.get('notebooks', []):
                    title = item.get('title') or item['id']
                    if title in info['notebooks'] and info['notebooks'][title] != item['id']:
                        title = f"{title} ({item['id'][:8]})"
                    info['notebooks'][title] = item['id']
                return len(info['notebooks'])
            print(f'{account}: {update(merge)} quaderni')
    elif x.cmd == 'notebook-add':
        def register(state):
            if x.account not in state['accounts']:
                p.error('Account sconosciuto')
            state['accounts'][x.account]['notebooks'][x.name] = x.id.split('/notebook/')[-1]
        update(register)
    elif x.cmd in ('notebook-create', 'source-add'):
        state = load()
        if not state['accounts']:
            p.error('Configura prima gli account')
        for source in x.sources:
            if '://' not in source and not Path(source).is_file():
                p.error(f'Fonte inesistente: {source}')
        name = x.name if x.cmd == 'notebook-create' else x.notebook
        if x.cmd == 'notebook-create':
            for account in state['accounts']:
                if name in state['accounts'][account]['notebooks']:
                    continue
                try:
                    result = json.loads(backend('create', name, '--json', profile=account))
                except subprocess.CalledProcessError as error:
                    detail = failure_payload(error).get('message') or last_line(error.stderr) or str(error)
                    say(f'{account}: creazione saltata ({detail})', error=True)
                    continue
                inner = result.get('notebook') if isinstance(result.get('notebook'), dict) else {}
                nbid = (result.get('id') or result.get('notebook_id') or inner.get('id')
                        or inner.get('name') or result.get('name'))
                if not nbid:
                    raise RuntimeError(f'ID quaderno assente nella risposta di {account}: {result}')
                update(lambda s, a=account, i=nbid: s['accounts'][a]['notebooks'].__setitem__(name, i))
        for account, info in load()['accounts'].items():
            nbid = info['notebooks'].get(name)
            if not nbid:
                say(f'{account}: quaderno {name} assente, fonti saltate', error=True)
                continue
            for source in x.sources:
                # Key local files by name: the same file has different paths on the Mac and in agent VMs.
                key = f'{account}:{nbid}:{source_key(source)}'
                done = {source_key_entry(entry) for entry in load().get('uploaded_sources', [])}
                if key in done:
                    continue
                # Large local files (e.g. a 3.5 MB summary) exceed the library's 30 s default.
                extra = ['--request-timeout', '600'] if '://' not in source else []
                try:
                    backend('source', 'add', source, '--notebook', nbid, *extra, profile=account)
                except subprocess.CalledProcessError as error:
                    detail = failure_payload(error).get('message') or last_line(error.stderr) or str(error)
                    say(f'{account}: fonte {source} non caricata ({detail})', error=True)
                    continue
                update(lambda s, k=key: s.setdefault('uploaded_sources', []).append(k))
                print(f'{account}: aggiunta {source}')
    elif x.cmd == 'notebook-list':
        for account, info in load()['accounts'].items():
            for name, nbid in info['notebooks'].items():
                print(f'{account}\t{name}\t{nbid}')
    elif x.cmd == 'queue-add':
        queue_add(x, p)
    elif x.cmd == 'mode':
        marker = ROOT / 'morpurgo-curated-shorts'
        if x.value == 'shorts':
            marker.touch(exist_ok=True)
            print('Modalità Shorts attiva per Morpurgo: gli invii lunghi restano in pausa.')
        else:
            marker.unlink(missing_ok=True)
            print('Modalità approfondimenti attiva per Morpurgo.')
    elif x.cmd == 'queue-list':
        jobs = select_jobs(load(), x.status, x.exam, x.notebook)
        if x.json:
            keys = ('id', 'status', 'account', 'notebook', 'exam', 'level', 'topic', 'parent', 'task_id')
            print(json.dumps([{**{key: job.get(key) for key in keys}, 'detail': job_detail(job)}
                              for job in jobs], ensure_ascii=False))
        else:
            for job in jobs:
                level = job['level'] if job.get('level') is not None else ''
                print(f"{job['status']}\t{job.get('account') or '*'}\t{job['notebook']}\t{job['id']}\t"
                      f"{job.get('exam') or ''}\t{level}\t{job.get('topic', '')}\t{job_detail(job)}")
    elif x.cmd == 'queue-retry':
        def retry(state):
            count = 0
            for job in state['jobs']:
                if (x.ids and job['id'] not in x.ids) or (not x.ids and job['status'] not in RETRYABLE):
                    continue
                if job['status'] not in RETRYABLE + ('pending',):
                    p.error(f"{job['id']} è {job['status']}: non si può ritentare")
                job['retry_count'] = job.get('retry_count', 0) + 1
                for key in ('error', 'retry_at', 'task_id'):
                    job.pop(key, None)
                release(state, job['id'], 'pending')
                count += 1
            return count
        print(f'{update(retry)} richieste rimesse in coda')
    elif x.cmd == 'queue-cancel':
        def cancel(state):
            count = 0
            for job in state['jobs']:
                if job['id'] in x.ids:
                    if job['status'] not in ('pending',) + RETRYABLE:
                        p.error(f"{job['id']} è {job['status']}: non si può annullare")
                    job['status'] = 'cancelled'
                    count += 1
            return count
        print(f'{update(cancel)} richieste annullate')
    elif x.cmd == 'status':
        summary = status_summary(load())
        if x.json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            for name, info in summary['accounts'].items():
                pause = f", in pausa fino a {info['paused_until']}" if info['paused_until'] else ''
                print(f"{name}: {info['notebooks']} quaderni, {info['in_production']} in produzione{pause}")
            print(('Lavori: ' + ', '.join(f'{k} {v}' for k, v in summary['jobs'].items()))
                  if summary['jobs'] else 'Coda vuota')
            for item in summary['attention']:
                print(f"! {item['status']} {item['id']} {item['topic'] or ''}: {item['detail']}")
    elif x.cmd == 'coverage':
        rows = coverage(load(), x.exam, x.notebook)
        if x.json:
            print(json.dumps(rows, ensure_ascii=False))
        else:
            for row in rows:
                counts = ' '.join(f'{s}={row[s]}' for s in STATUSES if row.get(s))
                level = row['level'] if row['level'] is not None else '-'
                print(f"{row['exam'] or '-'}\tL{level}\t{row['topic']}\t{counts}")
    elif x.cmd == 'syllabus-set':
        if not x.file.is_file():
            p.error(f'File inesistente: {x.file}')
        rows = load_syllabus(x.file)
        update(lambda s: s.setdefault('syllabi', {}).__setitem__(x.exam, rows))
        levels = sorted({r['level'] for r in rows})
        print(f'{x.exam}: {len(rows)} argomenti, livelli {levels}')
    elif x.cmd == 'syllabus-list':
        for exam, rows in load().get('syllabi', {}).items():
            if x.exam in (None, exam):
                for row in rows:
                    print(json.dumps(dict(row, exam=exam), ensure_ascii=False))
    elif x.cmd == 'next':
        state = load()
        if x.exam not in state.get('syllabi', {}):
            p.error(f'Nessun syllabus per {x.exam}: usa syllabus-set')
        rows = next_topics(state, x.exam, x.notebook, x.limit)
        if x.exam == 'Chimica Inorganica I — Morpurgo':
            rows = [dict(row, format='short' if shorts_only() else x.format) for row in rows]
        if not x.queue:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False))
        elif rows:
            # Temporary plan outside ~/.nbvideo: keeps the state folder clean and works on mounts without delete.
            plan = Path(tempfile.gettempdir()) / f'nbvideo-next-{uuid.uuid4().hex}.jsonl'
            plan.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows))
            try:
                ns = argparse.Namespace(notebook=x.notebook, topic=[], topics_file=plan, prompt=x.prompt, count=1,
                                        format=('short' if x.exam == 'Chimica Inorganica I — Morpurgo'
                                                and shorts_only() else x.format), language='it', exam=x.exam, level=None, parent=None,
                                        meta=None, account=None, allow_duplicates=False, force_depth=False, json=True)
                queue_add(ns, p)
            finally:
                plan.unlink(missing_ok=True)
        else:
            print(json.dumps({'added': [], 'skipped_duplicates': []}))
    elif x.cmd == 'run':
        run(x.max, x.json)
    elif x.cmd == 'drive-set-remote':
        if not re.fullmatch(r'[A-Za-z0-9_-]+', x.name):
            p.error('Nome remote rclone non valido')
        update(lambda s: s.__setitem__('drive_remote', x.name))
    elif x.cmd == 'drive-configure':
        subprocess.run(['rclone', 'config'], check=True)
    elif x.cmd == 'sync':
        raise SystemExit(subprocess.run([sys.executable, str(Path(__file__).with_name('sync.py'))]).returncode)
    elif x.cmd == 'schedule-install':
        subprocess.run([sys.executable, str(Path(__file__).with_name('install_schedule.py'))], check=True)


if __name__ == '__main__':
    try:
        main()
    except MissingBackend:
        print("Installa notebooklm-py: uv tool install 'notebooklm-py[browser]'", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as error:
        print(f"Comando fallito: {' '.join(map(str, error.cmd[1:]))}\n{(error.stderr or error.stdout or '').strip()}",
              file=sys.stderr)
        sys.exit(error.returncode or 1)
    except FileNotFoundError as error:
        print(f'Programma non trovato: {error.filename}', file=sys.stderr)
        sys.exit(1)
