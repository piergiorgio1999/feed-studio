#!/bin/sh
# Rigenera l'elenco dei video caricati e ripubblica il worker.
cd "$(dirname "$0")" && python3 - <<'PY'
import os
import json,re
ROOT=os.environ["FEED_DRIVE_ROOT"].rstrip("/")+"/"
vs=json.load(open(ROOT+"Gemini Notebook Video/index.json"))["videos"]
up=json.load(open("uploaded.json"))
out=[{"id":int(i),"exam":vs[int(i)].get("exam") or re.sub(r" \(Moodle\)| — fonti","",vs[int(i)]["notebook"]),"topic":vs[int(i)].get("topic") or "Ripasso","level":vs[int(i)].get("level"),"parent":vs[int(i)].get("parent")} for i in up]
json.dump(out,open("worker/src/videos.json","w"),ensure_ascii=False);print(len(out),"video")
PY
cp index.html worker/src/index.html && cd worker && wrangler deploy 2>&1 | tail -3
