# Protocollo per agenti (Codex, Claude)

Obiettivo: produrre in massa reel educativi (Video Overview di NotebookLM) che coprono il syllabus degli esami, partendo dai concetti essenziali e scendendo nel dettaglio un livello alla volta. I video finiscono su Drive e in `Gemini Notebook Video/index.json`, il feed di dumbscrolling.

La strategia, cioè cosa produrre e quando scendere di livello, la decidete voi. La CLI esegue, distribuisce il lavoro sui 4 account ed evita i doppioni.

## Regole

- Non leggere né modificare `~/.nbvideo/state.json` a mano. Usa i comandi: hanno un lock e sono sicuri anche con più agenti e `watch_until.py` attivi insieme.
- Per risparmiare token leggi solo `status --json` e `coverage --exam X --json`.
- Non assegnare gli account: un video senza `--account` va al primo account con quota libera. Se un account esaurisce la quota, i suoi video passano agli altri.
- Ogni richiesta porta sempre `--exam` e `--level` (1 = concetto essenziale, 2 = meccanismo o dettaglio, 3 = esercizio o trabocchetto d'esame, e così via); dal livello 2 in su anche `--parent`.

## Setup (una volta)

I profili NotebookLM esistono già sul Mac (`~/.notebooklm/profiles`: uno per account Google). Registrali senza rifare il login:

```sh
for a in <profilo1> <profilo2>; do ./nbvideo.py account-import $a; done
./nbvideo.py notebook-discover
```

Se una sessione scade, puoi rinnovarla dai cookie di Chrome: `notebooklm login --browser-cookies chrome --all-accounts --update`.

## Syllabus e regola di profondità

- `./nbvideo.py syllabus-set --exam "Chimica Fisica" syllabus.jsonl`: una riga per argomento, `{"topic", "level", "parent"}`. Dal livello 2 in su il padre è obbligatorio e deve esistere al livello precedente.
- `./nbvideo.py next --exam "Chimica Fisica" --notebook chimica [--limit N] [--queue]`: restituisce, o accoda con `--queue`, gli argomenti non ancora coperti il cui padre è già `uploaded`, partendo dai più superficiali.
- La regola la applica la CLI: `queue-add` rifiuta un argomento di livello N se il padre al livello N-1 non è `uploaded`. Si può forzare con `--force-depth`.

## Ciclo

1. `./nbvideo.py status --json`: quota per account, conteggi per esame e livello, problemi.
2. `./nbvideo.py coverage --exam "Chimica Fisica" --json`: argomenti già coperti, con stato.
3. Se l'esame ha un syllabus, usa `next --queue`. Altrimenti decidi tu i prossimi argomenti. Scendi di livello solo sui temi il cui livello precedente è `uploaded`.
4. Scrivi un piano `.jsonl` e accodalo in un colpo solo:
   ```sh
   ./nbvideo.py queue-add --notebook chimica --exam "Chimica Fisica" --topics-file piano.jsonl --json
   ```
   Una riga per argomento: `{"topic": "Arrhenius", "level": 2, "parent": "Cinetica", "prompt": "...", "format": "short"}`. I campi mancanti prendono i valori passati da riga di comando. Gli argomenti già coperti (stesso quaderno, esame, livello, argomento) vengono saltati e restituiti in `skipped_duplicates`.
5. `./nbvideo.py run --json`: invia in parallelo, un worker per account. Usa `--max N` per limitare il giro.
6. `./nbvideo.py sync`: scarica i video completati, li carica su Drive, aggiorna l'indice e marca `failed` le generazioni fallite.
7. Gestisci i problemi con `queue-retry [ID...]` e `queue-cancel ID...`.

## Stati

`pending` → `submitting` → `submitted` → `uploaded`. Possono anche finire in `failed` (Google), `needs_attention` (errore o invio interrotto: controlla il quaderno prima di ritentare) o `cancelled`.

Un account in pausa per quota mostra `paused_until` in `status`. Non serve fare nulla: `run` lo salta fino a quell'ora e `watch_until.py` si risveglia allora.


## Campagna Morpurgo Shorts

Finché esiste `~/.nbvideo/morpurgo-curated-shorts`, la CLI invia soltanto richieste `short` per Chimica Inorganica I — Morpurgo. `queue-add` e i piani JSONL vengono forzati a short; il prompt indica verticale 9:16, 45–60 secondi e un concetto per video. Il refill mantiene un backlog di 24; la coda lunga preesistente resta conservata e non viene inviata. Il refill torna a explainer dopo aver disattivato la campagna rimuovendo il marker.
