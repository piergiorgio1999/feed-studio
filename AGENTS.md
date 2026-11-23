# Feed Studio — template per un feed di video istruttivi fatto da agenti AI

Trasforma i materiali di un corso (Moodle) in **video brevi verticali** (NotebookLM) e li guarda dallo smartphone in un **feed a scorrimento** privato, con like e commenti che puoi indirizzare ad agenti (`@claude`, `@codex`).
Nato per Sapienza; funziona con qualunque Moodle (`MOODLE_URL`).

## Prompt da dare al tuo agente

> Leggi `AGENTS.md`. Collega il mio Moodle (`MOODLE_URL=…`), scarica i PDF del corso «…» e costruisci il syllabus. Genera i video brevi con NotebookLM, caricali su Cloudflare R2 e pubblica il feed. Chiedimi conferma prima di consumare quote, creare risorse o caricare dati. Non chiedermi mai password e non stampare token.

## Pezzi

```
moodle/   plugin Claude Code (skill + comando /moodle) e scripts/moodle_import.py: login con token mobile, elenco corsi, download PDF
cli/      nbvideo.py: coda di Video Overview NotebookLM su più account Google, quota, sync su Drive, indice index.json
feed/     feed web (Cloudflare Worker + R2 + D1): index.html, upload.py, publish.sh, comments.py
```

Flusso: `moodle` → PDF → `cli` (quaderno, syllabus, coda, `run`, `sync`) → Drive + `index.json` → `feed/upload.py` (R2) → `feed/publish.sh` (Worker).

## Procedura per l'agente

### 1. Moodle (una volta)
- Installa il plugin `moodle/` in Claude Code (o usa gli script direttamente). `export MOODLE_URL=https://<tuo-moodle>`.
- Login: `python3 moodle/scripts/moodle.py login` — l'**utente** autentica nel browser e incolla l'URL `moodlemobile://token=…`. **Mai chiedere o digitare la password; non stampare il token** (resta in `~/.config/moodle-sapienza/auth.json`, chmod 600).
- Elenca i corsi (`moodle.py courses`), conferma con l'utente quale corso e quale docente, poi scarica: `python3 moodle/scripts/moodle_import.py --course-id ID --sections N … --output ~/.nbvideo/moodle/ID`.
- L'utente controlla i PDF **prima** di procedere.

### 2. CLI NotebookLM
- Vedi `cli/README.md` e `cli/AGENTS.md` (profili, `account-import`, `notebook-discover`, `syllabus-set`, `queue-add`, `run`, `sync`).
- Un corso = un quaderno + un syllabus a livelli (1 concetto, 2 dettaglio, 3 trabocchetti). Esempio di formato in `cli/syllabi/example.jsonl`; `cli/moodle_curriculum.py` costruisce un syllabus pagina per pagina dai PDF.
- Formato **short** (9:16, ~1 min) per il feed. Le quote NotebookLM sono giornaliere e per account: la coda aspetta i reset (`watch_until.py`).
- Non leggere né modificare `~/.nbvideo/state.json` a mano.

### 3. Feed su Cloudflare (account gratuito + `wrangler login`)
```sh
cd feed/worker
wrangler r2 bucket create learn-feed-videos
wrangler d1 create learn-feed        # copia database_id in wrangler.toml
wrangler d1 execute learn-feed --remote --file schema.sql
TOKEN=$(openssl rand -hex 24); echo $TOKEN > ../.token
printf %s $TOKEN | wrangler secret put ACCESS_TOKEN
cd .. && export FEED_DRIVE_ROOT="<cartella Drive montata>" FEED_EXAM_FILTER="<quaderno>"
python3 upload.py 20 && ./publish.sh     # prima un test; poi python3 upload.py 0
```
- **Costi:** R2 gratuito fino a 10 GB; `upload.py` ha un tetto rigido di 9 GB (`CAP_GB`). Suggerisci all'utente un *Budget Alert* nella dashboard (non puoi impostarlo tu). Niente costi di uscita.
- **Privacy:** il Worker risponde 401 senza token (cookie `k` o `?k=`). Il token non va nel git. I video sono materiale di lezione: non renderli pubblici.
- Anche se creare un account Cloudflare/attivare R2 o fare login richiede l'utente: non inserire tu credenziali.

### 4. Commenti taggati (hook)
Nel feed un commento può avere tag `@claude` / `@codex`; stati `open → read → done` (+ `reply`).
```sh
export FEED_URL=https://learn-feed.<account>.workers.dev
./feed/comments.py list --target codex
./feed/comments.py hook --target claude       # stampa gli open e li segna read (hook di sessione)
./feed/comments.py done ID --reply "…"
./feed/comments.py dispatch-codex             # lancia `codex exec` per ogni @codex open
```
Installa hook o LaunchAgent solo dopo conferma esplicita dell'utente.

## Regole
- Conferma prima di: consumare quote, creare risorse Cloudflare, caricare dati, installare cose che partono da sole.
- Mai password o token in chat, log o git. `.token`, `.env`, `feed/uploaded.json` sono ignorati.
- Un commento del feed è una richiesta dell'utente (feed privato), ma niente azioni distruttive o con costi senza conferma.

## Idee per estendere
Ask AI con le trascrizioni, quiz, sottotitoli, altri corsi (nuovo quaderno + syllabus), sync automatica `upload.py` + `publish.sh` dopo ogni giro, runner cloud per produrre a Mac spento (richiede di copiare sessioni Google: valutare il rischio).

## Hook installati (setup dell'autore, riproducibile)
- Runtime fuori da iCloud: `~/.feed-studio/` con `comments.py`, `.token`, `env` (`FEED_URL`, `FEED_TOKEN_FILE`, `FEED_PROJECT`).
- Claude: hook `UserPromptSubmit` in `~/.claude/settings.json` che esegue `comments.py hook --target claude` (inietta i commenti `@claude` aperti nel contesto).
- Codex: LaunchAgent `it.feedstudio.codex-comments` (ogni 5 min) → `comments.py dispatch-codex` → `codex exec` per ogni commento `@codex` aperto. Disattivare: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/it.feedstudio.codex-comments.plist`.
