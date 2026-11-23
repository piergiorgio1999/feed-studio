#!/bin/sh
# Pubblica l'elenco dei video caricati (index/videos.json in R2) e, con --deploy, anche il Worker.
# Il Worker normalmente si pubblica da solo con GitHub Actions a ogni push su main.
# Richiede: FEED_DRIVE_ROOT (e per --deploy anche FEED_D1_ID). Vedi ~/.feed-studio/env.
cd "$(dirname "$0")" && python3 - <<'PY'
import json,os,re
ROOT=os.environ["FEED_DRIVE_ROOT"].rstrip("/")+"/"
vs=json.load(open(ROOT+"Gemini Notebook Video/index.json"))["videos"]
up=json.load(open("uploaded.json"))
out=[{"id":int(i),"exam":vs[int(i)].get("exam") or re.sub(r" \(Moodle\)| — fonti","",vs[int(i)]["notebook"]),"topic":vs[int(i)].get("topic") or "Ripasso","level":vs[int(i)].get("level"),"parent":vs[int(i)].get("parent")} for i in up]
json.dump(out,open(".videos.json","w"),ensure_ascii=False);print(len(out),"video")
PY
BUCKET="${FEED_BUCKET:-learn-feed-videos}"
wrangler r2 object put "$BUCKET/index/videos.json" --file .videos.json --content-type application/json --remote 2>&1 | tail -1
if [ "$1" = "--deploy" ]; then
  cp index.html worker/src/index.html && cd worker && sed "s/REPLACE_WITH_D1_ID/${FEED_D1_ID:?imposta FEED_D1_ID}/" wrangler.toml > wrangler.local.toml && wrangler deploy -c wrangler.local.toml 2>&1 | tail -3
fi
