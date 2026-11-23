---
name: moodle-sapienza
description: Esplora Moodle Sapienza (elearning.uniroma1.it) con l'account dell'utente - corsi a cui è iscritto, docenti, materiali e link dei corsi (es. Scienze Chimiche), notifiche, messaggi, scadenze, compiti, annunci dei forum, download di slide/dispense. Usa questa skill ogni volta che l'utente nomina Moodle, e-learning Sapienza, un corso o un prof dell'università, "cosa c'è di nuovo", scadenze o avvisi, oppure vuole generare, aggiornare o sincronizzare i link Moodle per il suo sito, anche se non dice esplicitamente "Moodle".
---

# Moodle Sapienza

Client: `${CLAUDE_PLUGIN_ROOT}/scripts/moodle.py` (Python stdlib, nessuna dipendenza). Parla con le Web Service API della app mobile Moodle (`moodle_mobile_app`), quindi vede ciò che vede l'app, e può combinarlo con automazioni.

## Autenticazione (una tantum)
Il token è in `~/.config/moodle-sapienza/auth.json`. Se manca (`Non sei autenticato`):
1. Esegui `python3 moodle.py login` e dai all'utente il link stampato: deve fare il login Sapienza da solo nel browser.
2. L'utente incolla l'URL `moodlemobile://token=...` (o il token) → `python3 moodle.py login --token "<...>"`.
**Mai chiedere o digitare la password dell'utente.** Non stampare il token in chat.

## Comandi
```
moodle.py info [--functions]        # utente, versione, funzioni abilitate
moodle.py courses [-q chimica]      # corsi iscritti
moodle.py search "chimica organica" # catalogo
moodle.py teachers <courseid>
moodle.py contents <courseid>       # sezioni, risorse, file (URL scaricabili)
moodle.py links -q chimica -o links.json   # tutti i link per il sito
moodle.py notifications | messages | calendar | assignments
moodle.py forums <courseid> [--forum ID]   # annunci dei prof
moodle.py download <url> [-o file]
moodle.py call <funzione> chiave=valore    # qualunque API, vedi `info --functions`
```
Output sempre JSON: elabora con `jq`/Python per automazioni (digest giornaliero, nuove scadenze, sync verso il sito).

## Flussi tipici
- **"Cosa c'è di nuovo?"** → `notifications`, `calendar`, poi `forums <id>` per i corsi rilevanti; riassumi per urgenza (scadenze prima).
- **Link per il sito** → `courses -q <parola>` per verificare che il filtro prenda i corsi giusti, poi `links -q <parola> -o links.json`. Mostra all'utente quanti corsi/link sono usciti prima di importarli, perché il filtro per nome può includere o perdere corsi.
- **Trovare un corso/prof** → `search` nel catalogo, poi `teachers <id>`.
- **Materiali** → `contents <id>`; i `fileurl` includono già il token, quindi `download` funziona senza sessione (non incollarli in pagine pubbliche: contengono il token).

## Note
- Il token dà accesso al tuo account: tienilo fuori da repository, log e dal sito pubblico. I link `/course/view.php` e `/mod/...` sono invece sicuri da pubblicare (richiedono comunque login).
- Se una funzione dà `accessexception`, il sito non l'ha abilitata: controlla `info --functions` e ripiega su un'alternativa.
- Sola lettura per default; non iscrivere/consegnare/inviare messaggi senza richiesta esplicita dell'utente.
- Per il sito: usa `links -o` e importa il JSON; rigenera periodicamente (cron/`/loop`).
