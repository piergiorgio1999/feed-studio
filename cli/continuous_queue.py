#!/usr/bin/env python3
"""Keep a source-grounded Morpurgo video backlog without duplicate jobs."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from nbvideo import ROOT, load, next_topics
from moodle_curriculum import DEFAULT_MANIFEST, pdf_pages

EXAM = 'Chimica Inorganica I — Morpurgo'
NOTEBOOK = 'Chimica Inorganica I — Morpurgo (Moodle)'
BACKLOG = 24
APP = Path(__file__).with_name('nbvideo.py')
REVIEW = re.compile(r'^Ripasso Morpurgo R(\d+):')
SHORTS_MARKER = ROOT / 'morpurgo-curated-shorts'


def cli(*args):
    result = subprocess.run([sys.executable, str(APP), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def review_plan(state):
    """Make deliberate retrieval practice after all distinct page topics are used."""
    rows = state['syllabi'][EXAM]
    parents = {job['topic'] for job in state['jobs']
               if job.get('exam') == EXAM and job['status'] == 'uploaded'
               and job.get('level') == 1}
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    by_name = {Path(item['file']).name.casefold(): item for item in manifest['files']}
    rounds = [int(match.group(1)) for job in state['jobs']
              if job.get('exam') == EXAM
              for match in [REVIEW.match(job.get('topic', ''))] if match]
    round_no = max(rounds, default=0) + 1
    modes = (
        ('interrogazione', 'Formula domande orali progressive e rispondi mostrando ogni passaggio.'),
        ('errori', 'Costruisci un ripasso che corregga i fraintendimenti più probabili, spiegando perché sono sbagliati.'),
        ('applicazione', 'Applica le relazioni presenti a un caso guidato; se mancano dati, esplicita il limite.'),
        ('collegamenti', 'Collega il tema alle lezioni vicine e mostra come riconoscere quando usare ciascun modello.'),
    )
    mode, instruction = modes[(round_no - 1) % len(modes)]
    plan = []
    for row in rows:
        parent = row.get('topic', '')
        if row.get('level') != 1 or not parent.startswith('Morpurgo lezione ') or parent not in parents:
            continue
        match = re.search(r'dispensa Moodle\s+([^\s]+\.pdf)', row.get('prompt', ''), re.I)
        if not match or match.group(1).casefold() not in by_name:
            raise RuntimeError(f'PDF del ripasso non identificato: {parent}')
        filename = match.group(1)
        source = DEFAULT_MANIFEST.parent / by_name[filename.casefold()]['file']
        pages = pdf_pages(source)
        page = (round_no - 1) % pages + 1
        plan.append({'topic': f'Ripasso Morpurgo R{round_no}: {parent} — p. {page} ({mode})',
                     'level': 2, 'parent': parent, 'format': 'short' if SHORTS_MARKER.exists() else 'explainer',
                     'prompt': f'Ripasso attivo distinto, ciclo {round_no}. Usa soprattutto {filename}, pagina {page}. '
                               f'{instruction} Evita una copia del video panoramico: concentra la risposta sui '
                               'dettagli verificabili della pagina e segnala ciò che non è nelle fonti.'})
    return plan


def main():
    state = load()
    if EXAM not in state.get('syllabi', {}):
        print('Curriculum Morpurgo non registrato; nessuna nuova richiesta')
        return 0
    pending = sum(job.get('exam') == EXAM and job['status'] == 'pending'
                  for job in state['jobs'])
    budget = max(0, BACKLOG - pending)
    if not budget:
        print(f'Coda Morpurgo pronta: {pending} richieste')
        return 0
    ready = next_topics(state, EXAM, NOTEBOOK, budget)
    # In the active Shorts campaign, even Moodle page-by-page refreshes must
    # stay short. Keep the syllabus coverage order and change only the format.
    active_shorts = SHORTS_MARKER.exists()
    ready = [dict(row, format='short' if active_shorts else 'explainer') for row in ready]
    if ready:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.jsonl', delete=False) as handle:
            temporary = Path(handle.name)
            for row in ready:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        try:
            output = cli('queue-add', '--notebook', NOTEBOOK, '--exam', EXAM,
                         '--topics-file', str(temporary), '--json')
        finally:
            temporary.unlink(missing_ok=True)
        result = json.loads(output)
        print(f"Aggiunti {len(result.get('added', []))} Shorts Moodle; coda precedente {pending}")
        return 0
    # A review round is queued only when the previous round has finished.
    if any(job.get('exam') == EXAM and REVIEW.match(job.get('topic', ''))
           and job['status'] in ('pending', 'submitting', 'submitted') for job in state['jobs']):
        return 0
    plan = review_plan(state)
    if not plan:
        print('Nessuna lezione Morpurgo consolidata per il ripasso')
        return 0
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.jsonl', delete=False) as handle:
        temporary = Path(handle.name)
        for item in plan:
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')
    try:
        output = cli('queue-add', '--notebook', NOTEBOOK, '--exam', EXAM,
                     '--topics-file', str(temporary), '--json')
        result = json.loads(output)
        print(f"Aggiunti {len(result.get('added', []))} Shorts di ripasso Morpurgo")
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'Rifornimento coda incompleto: {exc}', file=sys.stderr)
        raise SystemExit(1)
