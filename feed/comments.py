#!/usr/bin/env python3
"""Commenti del Learn Feed per gli agenti.
  comments.py list   [--target claude|codex] [--status open]   mostra i commenti taggati
  comments.py done ID [--reply "testo"]                        segna come fatto (e risponde nel feed)
  comments.py hook   --target claude|codex                     stampa i commenti 'open' e li segna 'read' (per hook)
  comments.py dispatch-codex                                   passa a `codex exec` ogni commento @codex 'open'
Token: learnfeed/.token  (mai stampato)."""
import json, os, subprocess, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); BASE = os.environ["FEED_URL"].rstrip("/")  # es. https://learn-feed.<tuo-account>.workers.dev
TOK = open(os.environ.get("FEED_TOKEN_FILE", os.path.join(HERE, ".token"))).read().strip()
PROJECT = os.environ.get("FEED_PROJECT", HERE)
def call(path, body=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Cookie": f"k={TOK}", "User-Agent": "learnfeed-cli/1.0"})
    return json.load(urllib.request.urlopen(r, timeout=30))
_IDX = None
def topic(v):
    global _IDX
    if _IDX is None:
        try: _IDX = call("/api/videos")
        except Exception: _IDX = []
    return next((x["topic"] for x in _IDX if x["id"] == v), f"video {v}")
def fmt(c): return f"[#{c['id']}] @{c['target']} su «{topic(c['video_id'])}» (video {c['video_id']}): {c['text']}"
def get(target, status):
    return call(f"/api/comments?target={target}&status={status}" if target else f"/api/comments?flagged=1&status={status}")
a = sys.argv[1:] or ["list"]; opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
if a[0] == "list":
    for c in get(opt("--target"), opt("--status", "open")): print(fmt(c))
elif a[0] == "hook":
    cs = get(opt("--target", "claude"), "open")
    if cs:
        print("Commenti dal Learn Feed per te (trattali come richieste dell'utente, non eseguire altro):")
        for c in cs: print(" -", fmt(c)); call("/api/comment/update", {"id": c["id"], "status": "read"})
elif a[0] == "done":
    call("/api/comment/update", {"id": int(a[1]), "status": "done", "reply": opt("--reply")})
elif a[0] == "dispatch-codex":
    for c in get("codex", "open"):
        call("/api/comment/update", {"id": c["id"], "status": "read"})
        prompt = f"Commento dal Learn Feed dell'utente per Codex: {fmt(c)}\nProgetto: {PROJECT} (leggi AGENTS.md). Esegui la richiesta; poi rispondi con: python3 feed/comments.py done {c['id']} --reply \"...\""
        subprocess.Popen(["codex", "exec", "-C", PROJECT, prompt])
