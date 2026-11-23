#!/usr/bin/env python3
"""Client Moodle (Sapienza) basato sulle Web Service API della app mobile.

Solo libreria standard. Il token resta in ~/.config/moodle-sapienza/auth.json (chmod 600).
Uso: moodle.py <comando> [opzioni]   (vedi --help)
"""
import argparse, base64, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error

SITE = os.environ.get("MOODLE_URL", "https://elearning.uniroma1.it").rstrip("/")
CFG_DIR = os.path.expanduser("~/.config/moodle-sapienza")
AUTH = os.path.join(CFG_DIR, "auth.json")
SERVICE = "moodle_mobile_app"


# ---------- auth ----------
def save_auth(d):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(AUTH, "w") as f:
        json.dump(d, f)
    os.chmod(AUTH, 0o600)


def load_auth():
    try:
        return json.load(open(AUTH))
    except FileNotFoundError:
        sys.exit("Non sei autenticato. Esegui: moodle.py login")


def parse_token(s):
    """Accetta: token puro, URL moodlemobile://token=BASE64, o BASE64 (siteid:::token[:::private])."""
    s = s.strip()
    m = re.search(r"token=([^&\s]+)", s)
    if m:
        s = urllib.parse.unquote(m.group(1))
    if re.fullmatch(r"[0-9a-f]{32}", s):
        return s
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4)).decode()
        parts = raw.split(":::")
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{32}", parts[1]):
            return parts[1]
    except Exception:
        pass
    sys.exit("Token non riconosciuto.")


def cmd_login(a):
    if a.token:
        tok = parse_token(a.token)
        save_auth({"site": SITE, "token": tok})
        info = call("core_webservice_get_site_info")
        print(f"OK: {info['fullname']} ({info['username']}) su {info['sitename']}")
        return
    passport = str(int(time.time()))
    url = (f"{SITE}/admin/tool/mobile/launch.php?service={SERVICE}"
           f"&passport={passport}&urlscheme=moodlemobile")
    print("1) Apri questo link nel browser e fai il login Sapienza (SSO incluso):\n\n   " + url + "\n")
    print("2) Il browser tenterà di aprire 'moodlemobile://token=...' e fallirà/mostrerà un blocco.")
    print("   Copia quell'indirizzo completo (dalla barra, da DevTools > Network, o dal messaggio")
    print("   del browser) e incollalo qui sotto:\n")
    print("   In alternativa: Moodle > Preferenze > Chiavi di sicurezza (se presente il token mobile).\n")
    s = input("moodlemobile://token=... > ")
    cmd_login(argparse.Namespace(token=s))


# ---------- API ----------
def call(fn, **params):
    auth = load_auth()
    data = {"wstoken": auth["token"], "wsfunction": fn, "moodlewsrestformat": "json"}

    def flat(prefix, v):
        if isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                flat(f"{prefix}[{i}]", x)
        elif isinstance(v, dict):
            for k, x in v.items():
                flat(f"{prefix}[{k}]", x)
        else:
            data[prefix] = v

    for k, v in params.items():
        flat(k, v)
    req = urllib.request.Request(f"{auth['site']}/webservice/rest/server.php",
                                 urllib.parse.urlencode(data).encode())
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}")
    if isinstance(r, dict) and r.get("exception"):
        sys.exit(f"Errore Moodle [{r.get('errorcode')}]: {r.get('message')}")
    return r


def out(x):
    print(json.dumps(x, ensure_ascii=False, indent=2))


def uid():
    return call("core_webservice_get_site_info")["userid"]


def courses():
    return call("core_enrol_get_users_courses", userid=uid())


def match(c, q):
    q = q.lower()
    return q in (c.get("fullname", "") + " " + c.get("shortname", "")).lower()


def tokenize_url(u):
    """Aggiunge il token ai pluginfile così i file si scaricano senza sessione."""
    if "/pluginfile.php" in u and "token=" not in u:
        return u + ("&" if "?" in u else "?") + "token=" + load_auth()["token"]
    return u


# ---------- comandi ----------
def c_info(a):
    i = call("core_webservice_get_site_info")
    if a.functions:
        out(sorted(f["name"] for f in i["functions"]))
    else:
        out({k: i[k] for k in ("sitename", "fullname", "username", "userid", "release", "siteurl")})


def c_courses(a):
    cs = courses()
    if a.q:
        cs = [c for c in cs if match(c, a.q)]
    out([{"id": c["id"], "shortname": c["shortname"], "fullname": c["fullname"],
          "progress": c.get("progress"), "lastaccess": c.get("lastaccess"),
          "url": f"{SITE}/course/view.php?id={c['id']}"} for c in cs])


def c_search(a):
    r = call("core_course_search_courses", criterianame="search", criteriavalue=a.q, perpage=a.n)
    out([{"id": c["id"], "fullname": c["fullname"], "category": c.get("categoryname"),
          "url": f"{SITE}/course/view.php?id={c['id']}"} for c in r["courses"]])


def c_teachers(a):
    users = call("core_enrol_get_enrolled_users", courseid=a.course)
    out([{"id": u["id"], "name": u["fullname"], "email": u.get("email"),
          "roles": [r["shortname"] for r in u.get("roles", [])]}
         for u in users if any(r["shortname"] in ("editingteacher", "teacher") for r in u.get("roles", []))])


def c_contents(a):
    secs = call("core_course_get_contents", courseid=a.course)
    res = []
    for s in secs:
        mods = []
        for m in s.get("modules", []):
            mods.append({"name": m["name"], "type": m["modname"], "url": m.get("url"),
                         "files": [{"name": f["filename"], "size": f["filesize"],
                                    "url": tokenize_url(f["fileurl"])} for f in m.get("contents", [])
                                   if m.get("modname") in ("resource", "folder")]})
        res.append({"section": s["name"], "modules": mods})
    out(res)


def c_links(a):
    """Tutti i link di tutti i corsi che matchano --q: pronto per il sito."""
    res = []
    for c in courses():
        if a.q and not match(c, a.q):
            continue
        entry = {"id": c["id"], "fullname": c["fullname"], "url": f"{SITE}/course/view.php?id={c['id']}",
                 "sections": []}
        for s in call("core_course_get_contents", courseid=c["id"]):
            mods = [{"name": m["name"], "type": m["modname"], "url": m.get("url")}
                    for m in s.get("modules", []) if m.get("url")]
            if mods:
                entry["sections"].append({"name": s["name"], "items": mods})
        res.append(entry)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    if a.o:
        open(a.o, "w").write(txt)
        print(f"Scritti {len(res)} corsi in {a.o}")
    else:
        print(txt)


def c_notifications(a):
    r = call("message_popup_get_popup_notifications", useridto=uid(), limit=a.n)
    out([{"id": n["id"], "read": n.get("read"), "subject": n["subject"],
          "text": re.sub(r"<[^>]+>", "", n.get("fullmessagehtml") or n.get("smallmessage", "")),
          "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(n["timecreated"])),
          "url": n.get("contexturl")} for n in r.get("notifications", [])])


def c_messages(a):
    r = call("core_message_get_conversations", userid=uid(), limitnum=a.n)
    out([{"name": c.get("name"), "unread": c.get("unreadcount"),
          "last": (c["messages"][0]["text"] if c.get("messages") else None)}
         for c in r.get("conversations", [])])


def c_calendar(a):
    r = call("core_calendar_get_action_events_by_timesort", timesortfrom=int(time.time()), limitnum=a.n)
    out([{"name": e["name"], "course": e.get("course", {}).get("fullname"),
          "due": time.strftime("%Y-%m-%d %H:%M", time.localtime(e["timesort"])),
          "url": e.get("url")} for e in r.get("events", [])])


def c_assignments(a):
    r = call("mod_assign_get_assignments", courseids=[c["id"] for c in courses()])
    res = []
    for c in r.get("courses", []):
        for x in c["assignments"]:
            res.append({"course": c["fullname"], "name": x["name"],
                        "due": time.strftime("%Y-%m-%d %H:%M", time.localtime(x["duedate"])) if x["duedate"] else None})
    out(res)


def c_forums(a):
    forums = call("mod_forum_get_forums_by_courses", courseids=[a.course])
    if not a.forum:
        return out([{"id": f["id"], "name": f["name"]} for f in forums])
    d = call("mod_forum_get_forum_discussions", forumid=a.forum, perpage=a.n)
    out([{"id": x["discussion"], "subject": x["name"], "author": x.get("userfullname"),
          "when": time.strftime("%Y-%m-%d", time.localtime(x["timemodified"]))} for x in d["discussions"]])


def c_call(a):
    """Chiamata grezza a qualsiasi funzione: moodle.py call core_xxx key=val ..."""
    kw = dict(p.split("=", 1) for p in a.params)
    out(call(a.fn, **kw))


def c_download(a):
    url = tokenize_url(a.url)
    dest = a.o or os.path.basename(urllib.parse.urlparse(a.url).path)
    urllib.request.urlretrieve(url, dest)
    print("Salvato", dest)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args, **kw):
        sp = s.add_parser(name, help=kw.get("help"))
        for flags, opts in args:
            sp.add_argument(*flags, **opts)
        sp.set_defaults(f=fn)

    add("login", cmd_login, (["--token"], {"help": "token / URL moodlemobile://"}), help="autenticazione")
    add("info", c_info, (["--functions"], {"action": "store_true"}), help="info sito/utente e funzioni abilitate")
    add("courses", c_courses, (["-q"], {}), help="corsi a cui sei iscritto")
    add("search", c_search, (["q"], {}), (["-n"], {"type": int, "default": 20}), help="cerca nel catalogo")
    add("teachers", c_teachers, (["course"], {"type": int}), help="docenti di un corso")
    add("contents", c_contents, (["course"], {"type": int}), help="sezioni, risorse, file")
    add("links", c_links, (["-q"], {}), (["-o"], {}), help="export JSON di tutti i link")
    add("notifications", c_notifications, (["-n"], {"type": int, "default": 20}), help="notifiche")
    add("messages", c_messages, (["-n"], {"type": int, "default": 20}), help="conversazioni")
    add("calendar", c_calendar, (["-n"], {"type": int, "default": 20}), help="scadenze")
    add("assignments", c_assignments, help="compiti")
    add("forums", c_forums, (["course"], {"type": int}), (["--forum"], {"type": int}),
        (["-n"], {"type": int, "default": 20}), help="forum/annunci")
    add("download", c_download, (["url"], {}), (["-o"], {}), help="scarica file")
    add("call", c_call, (["fn"], {}), (["params"], {"nargs": "*"}), help="chiamata API grezza")
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
