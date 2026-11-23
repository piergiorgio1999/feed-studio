#!/usr/bin/env python3
"""Scarica i titoli specifici che NotebookLM dà a ogni video (sola lettura) -> titles.json {artifact_id: titolo}.
Serve la CLI cli/nbvideo.py (stato in ~/.nbvideo) e `notebooklm` con i profili già collegati."""
import json, os, subprocess, sys
sys.path.insert(0, os.environ.get("NBVIDEO_DIR", os.path.expanduser("~/Documents/ChatGPT/liberiamo sapienza stuff/notebook-video-cli")))
from nbvideo import load
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "titles.json")
titles = json.load(open(OUT)) if os.path.exists(OUT) else {}
pairs = {(j["account"], j["notebook_id"]) for j in load()["jobs"] if j["status"] == "uploaded" and j.get("task_id") and j.get("notebook_id")}
for acc, nb in sorted(pairs):
    r = subprocess.run(["notebooklm", "--profile", acc, "artifact", "list", "--type", "video", "--json", "-n", nb], capture_output=True, text=True)
    try: arts = json.loads(r.stdout); arts = arts.get("artifacts", arts)
    except Exception: print("salto", acc, nb[:8]); continue
    if not isinstance(arts, list): print("risposta inattesa", acc, nb[:8]); continue
    for a in arts:
        if a.get("title"): titles[a["id"]] = a["title"]
json.dump(titles, open(OUT, "w"), ensure_ascii=False); print(len(titles), "titoli")
