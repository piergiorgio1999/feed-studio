#!/usr/bin/env python3
"""Carica N video in R2 con tetto di spazio rigido. Uso: python3 upload.py N  (N=0 -> tutti)
Si ferma PRIMA di superare CAP_GB (default 9, sotto i 10 GB gratuiti). Salta i video già caricati."""
import json, os, subprocess, sys
ROOT = os.environ["FEED_DRIVE_ROOT"]   # es. ~/Library/CloudStorage/GoogleDrive-.../Il mio Drive
BUCKET = os.environ.get("FEED_BUCKET", "learn-feed-videos")
FILTER = os.environ.get("FEED_EXAM_FILTER", "")   # sottostringa del quaderno; vuoto = tutti
CAP = float(os.environ.get("CAP_GB", "9")) * 1e9
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "uploaded.json")   # {id: size}
n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
vids = json.load(open(os.path.join(ROOT, "Gemini Notebook Video/index.json")))["videos"]
done = json.load(open(STATE)) if os.path.exists(STATE) else {}
used = sum(done.values())
count = 0
for i, v in enumerate(vids):
    if n and count >= n: break
    p = os.path.join(ROOT, v["drive_path"])
    if v.get("format") != "short" or FILTER not in v["notebook"]: continue  # solo brevi/verticali (e filtro quaderno)
    if str(i) in done or not os.path.isfile(p): continue
    size = os.path.getsize(p)
    if used + size > CAP:
        print(f"STOP: tetto {CAP/1e9:.1f} GB raggiunto ({used/1e9:.2f} GB usati)"); break
    r = subprocess.run(["wrangler", "r2", "object", "put", f"{BUCKET}/v/{i}.mp4", "--file", p,
                        "--content-type", "video/mp4", "--remote"], capture_output=True, text=True)
    if r.returncode:
        print("ERRORE:", (r.stdout + r.stderr)[-600:]); break
    done[str(i)] = size; used += size; count += 1
    json.dump(done, open(STATE, "w"))
    print(f"ok {i} ({used/1e9:.2f} GB totali)")
print(f"Fine: {len(done)} video, {used/1e9:.2f} GB")
