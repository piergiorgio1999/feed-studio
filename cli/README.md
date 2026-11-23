# Notebook Video CLI

CLI locale per accodare Video Overview di NotebookLM su fino a quattro account Google, pensata per essere pilotata da agenti: vedi [AGENTS.md](AGENTS.md). Usa [notebooklm-py](https://github.com/teng-lin/notebooklm-py) per login, profili e generazione. La coda registra l'invio delle richieste, non il completamento dei video.

## Installazione

```sh
uv tool install 'notebooklm-py[browser]'
uv tool run --from 'notebooklm-py[browser]' playwright install chromium
brew install rclone
cd notebook-video-cli
chmod +x nbvideo.py
```

## Uso

```sh
./nbvideo.py account-add personale
./nbvideo.py account-add universita
./nbvideo.py notebook-create chimica ./dispense.pdf ./appunti.md
notebooklm --profile personale list --json
./nbvideo.py notebook-add chimica ID_DEL_QUADERNO --account personale
./nbvideo.py notebook-add chimica ID_DEL_QUADERNO --account universita
./nbvideo.py queue-add --notebook chimica --prompt 'Spiega un argomento alla volta in italiano, con esempi da esame.' --count 4
./nbvideo.py queue-list
./nbvideo.py run
```

Se il login con Chromium si chiude (succede su macOS 15+), usa `./nbvideo.py account-add personale --browser chrome`.

## Stati della coda

`queue-list` mostra stato, account, quaderno, ID, argomento e dettaglio (ora di ripresa o ultimo errore).

- `pending`: da inviare, al primo account con quota libera.
- `submitting`: un worker lo sta inviando in questo momento.
- `submitted`: richiesta accettata da Google, video in produzione.
- `uploaded`: MP4 scaricato e caricato su Drive da `sync`.
- `failed`: Google ha segnalato la generazione come fallita (rilevato da `sync`).
- `cancelled`: annullato con `queue-cancel`.
- `needs_attention`: errore non legato alla quota, oppure invio interrotto a metà (controlla il quaderno prima di riprovare, per evitare doppioni).

Per rimettere in coda `failed` e `needs_attention` usa `./nbvideo.py queue-retry`; per lavori specifici `./nbvideo.py queue-retry ID1 ID2`.

Per ottenere video distinti, prepara un file `argomenti.txt` con un tema per riga e usa:

```sh
./nbvideo.py queue-add --notebook chimica --topics-file argomenti.txt --count 1 --format short --prompt 'Spiega in italiano con un esempio e una domanda da esame.'
```

`--count` indica quanti video richiedere per ciascun argomento. Senza `--topics-file`, più richieste useranno lo stesso prompt.

`notebook-create` crea il quaderno in tutti gli account configurati e carica in ciascuno le stesse fonti locali o URL. I nomi/ID vengono registrati dopo ogni creazione. Per aggiungere materiale in seguito, usa `./nbvideo.py source-add chimica ./altra-dispensa.pdf`.

Per distribuire richieste sui quattro account, registra lo stesso nome di quaderno in ciascuno. Le richieste non sono legate a un account: `run` avvia un worker in parallelo per ogni account e ciascuno prende il prossimo video disponibile. Quando un account esaurisce la quota si mette in pausa fino allo sblocco e gli altri continuano. Usa `--account` solo per forzarne uno. Tutti i comandi bloccano `state.json` durante le modifiche, quindi più agenti, `run` e `watch_until.py` possono lavorare insieme.

Per gli agenti: `queue-add` accetta `--exam`, `--level`, `--parent`, `--meta` e piani `.jsonl`, e salta gli argomenti già coperti. `status --json` e `coverage --json` danno un riepilogo compatto. `queue-cancel` annulla le richieste non ancora inviate. I profili di notebooklm-py contengono sessioni Google: non copiarli in un repository. Per scaricare i risultati usa `notebooklm --profile NOME download video --all -n ID`.

## Upload automatico dei video su Google Drive

Richiede [rclone](https://rclone.org/) installato e un remote Google Drive configurato localmente; non inserire token, file `rclone.conf` o credenziali nel repository.

1. Avvia `./nbvideo.py drive-configure` e scegli `n` per un nuovo remote.
2. Assegna un nome, per esempio `gdrive`, scegli il backend `drive` (Google Drive) e completa il login OAuth nel browser. Per un uso personale, i valori avanzati possono restare predefiniti.
3. Controlla i remote configurati con `rclone listremotes`. La configurazione e i token OAuth restano nel percorso privato gestito da rclone (normalmente `~/.config/rclone/rclone.conf`).

Configura il remote nella CLI e installa il controllo periodico macOS:

```sh
./nbvideo.py drive-set-remote gdrive
./nbvideo.py sync
./nbvideo.py schedule-install
```

Per seguire la produzione fino alle 08:00 successive (stamattina se lo avvii di notte), usa `python3 watch_until.py`. A ogni giro registra i quaderni degli account (`notebook-discover`), invia la coda (`run`) e sincronizza. Il controllo successivo viene fissato alla scadenza della finestra di utilizzo riportata da Google, più dieci minuti. La sincronizzazione dei video completati avviene all'avvio e a ogni scadenza; se Google non fornisce una scadenza, il programma lo segnala senza inventarne una.

`sync.py` controlla i task accodati completati, scarica gli MP4 e li carica in `gdrive:Gemini Notebook Video/chimica/`. `install_schedule.py` copia gli script in `~/.nbvideo/app` (Documenti è in iCloud: i file possono essere scaricati solo nel cloud e macOS blocca l'accesso in background) e registra un LaunchAgent che avvia `sync` da lì al login e ogni 12 ore mentre il Mac è acceso. Dopo ogni modifica agli script rilancia `./nbvideo.py schedule-install`. Log in `~/.nbvideo/sync.log` e `sync-error.log`. Il LaunchAgent sincronizza soltanto: non invia nuove richieste. Se era spento, il controllo al login recupera i video mancanti. I file già caricati sono marcati `uploaded`; `rclone copyto --checksum` evita duplicati invariati. Per un MP4 manuale resta disponibile `python3 drive_upload.py ./video.mp4 --remote gdrive --notebook chimica`. La sincronizzazione riguarda tutti i video completati nei quaderni registrati (anche quelli creati a mano in NotebookLM); i lavori della coda corrispondenti vengono marcati `uploaded`. Nota: `watch_until.py` esegue `notebook-discover`, che registra tutti i quaderni degli account.

Il Google Drive Sapienza deve essere l'account autenticato nel remote `gdrive`. In `Gemini Notebook Video/index.json` viene scritto un indice con quaderno, esame, livello, argomento, formato, account e percorso dei video caricati, ordinato per esame e poi per livello (prima le basi). Il prossimo feed dumb scroll potrà leggerlo tramite un collegamento autorizzato al Drive; l'indice non rende pubblici i file.

L'API ufficiale Gemini Notebook non documenta ancora la generazione dei Video Overview; notebooklm-py usa un'interfaccia non ufficiale che può cambiare. Verificata con notebooklm-py 0.8.6 (comandi, opzioni e formato JSON) e con una simulazione completa del flusso; non ancora su account Google reali.
# Importazione Moodle Sapienza

`moodle_import.py` usa la sessione Moodle già configurata da Claude in
`~/.config/moodle-sapienza/auth.json`. Scarica le dispense PDF delle sezioni
indicate e scrive un manifest con titolo della lezione e checksum, senza
registrare il token. Per il corso di Chimica Inorganica I di Morpurgo:

```sh
python3.11 moodle_import.py --course-id 7633 --sections 1 2 --output ~/.nbvideo/moodle/7633
```

Il piano `syllabi/morpurgo-moodle-7633.jsonl` contiene un Video Overview per il
programma e per ognuna delle 41 lezioni del Moodle. I due libri facoltativi
sono esclusi dall'importazione predefinita.

`moodle_curriculum.py` estende quel piano con due approfondimenti per ogni
pagina delle dispense (spiegazione e domanda d'esame). Dopo la registrazione
con `syllabus-set`, il LaunchAgent `it.nbvideo.queue-watch` mantiene una coda
di 24 lavori, invia quelli disponibili alla scadenza della quota Google più
10 minuti e ritenta gli errori di rete. Esauriti gli argomenti distinti,
aggiunge ripassi attivi identificati per ciclo, lezione e pagina. I PDF restano
le fonti di ogni video; il processo non promette contenuti nuovi quando una
fonte non li contiene.



## Modalità Shorts Morpurgo

Il comando `nbvideo mode shorts` attiva la produzione curatoriale Shorts per Chimica Inorganica I; `nbvideo mode explainer` ripristina i video approfonditi. La modalità si può controllare o cambiare esplicitamente senza editare file. Il marker `~/.nbvideo/morpurgo-curated-shorts` attiva la produzione curatoriale per Chimica Inorganica I: il CLI invia solo job `short` e sospende senza cancellarli i job `explainer` pendenti. `queue-add`, compresi i piani JSONL, forza anch'esso il formato short per Morpurgo. `next --queue` e il refill rispettano il marker; senza marker gli altri corsi e le modalità lunghe conservano il formato explainer. I generatori automatici producono richieste per una singola pagina. Il prompt aggiunge indicazioni per video verticale 9:16 da 45-60 secondi, un concetto, un diagramma chiaro e zero formule non presenti nella fonte. La durata e l'orientamento finali dipendono comunque dall'output di NotebookLM.
