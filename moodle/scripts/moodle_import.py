#!/usr/bin/env python3
"""Download PDF resources from selected Sapienza Moodle course sections."""

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_FILE = Path.home() / ".config/moodle-sapienza/auth.json"
MANIFEST_NAME = "manifest.json"


def safe_name(value, fallback="file"):
    value = re.sub(r"[^\w. -]+", "_", value, flags=re.UNICODE).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return (value[:150] or fallback)


def rest_call(site, token, function, **params):
    query = {
        "wstoken": token,
        "wsfunction": function,
        "moodlewsrestformat": "json",
        **params,
    }
    request = urllib.request.Request(
        site.rstrip("/") + "/webservice/rest/server.php",
        data=urllib.parse.urlencode(query).encode(),
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.load(response)
    if isinstance(result, dict) and result.get("exception"):
        raise RuntimeError("Moodle REST returned an API error")
    return result


def fetch(url):
    # Resource URLs contain Moodle's file authorization token. Keep URL out of errors/logs/manifests.
    request = urllib.request.Request(url, headers={"User-Agent": "moodle-import/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read(), response.headers.get("Content-Type", "")


def load_manifest(path):
    if not path.exists():
        return {"course_id": None, "files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Existing manifest.json is unreadable; refusing to overwrite") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files", []), list):
        raise RuntimeError("Existing manifest.json has an unexpected format")
    data.setdefault("files", [])
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-id", type=int, required=True)
    parser.add_argument("--sections", type=int, nargs="+", required=True,
                        help="Moodle section numbers (not display positions)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-textbooks", action="store_true",
                        help="include obvious supplementary textbook resources")
    args = parser.parse_args()

    try:
        auth = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        site, token = auth["site"].rstrip("/"), auth["token"]
        if not site.startswith("https://") or not token:
            raise ValueError
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        print(f"Moodle credentials unavailable or invalid in {AUTH_FILE}", file=sys.stderr)
        return 2

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / MANIFEST_NAME
    try:
        contents = rest_call(site, token, "core_course_get_contents", courseid=args.course_id)
        if not isinstance(contents, list):
            raise RuntimeError("Unexpected Moodle course contents response")
        manifest = load_manifest(manifest_path)
        if manifest.get("course_id") not in (None, args.course_id):
            raise RuntimeError("Output manifest belongs to a different course")
        manifest["course_id"] = args.course_id
        manifest.setdefault("files", [])
        known = {entry.get("origin"): entry for entry in manifest["files"] if isinstance(entry, dict)}
        chosen = set(args.sections)
        sections = [section for section in contents if section.get("section") in chosen]
        found = {section.get("section") for section in sections}
        missing = chosen - found
        if missing:
            raise RuntimeError("Section number(s) not found: " + ", ".join(map(str, sorted(missing))))

        downloaded = skipped = 0
        for section in sections:
            section_title = section.get("name") or f"Section {section.get('section')}"
            section_dir = output / f"{section.get('section', 0):02d}-{safe_name(section_title)}"
            section_dir.mkdir(parents=True, exist_ok=True)
            for module in section.get("modules", []):
                lesson = module.get("name") or "Untitled resource"
                if module.get("modname") != "resource":
                    continue
                if not args.include_textbooks and re.search(r"pauling|eyring|walter|kimball|textbook|libro di testo|approfondiment", lesson, re.I):
                    continue
                for item in module.get("contents", []):
                    if item.get("type") != "file" or not str(item.get("filename", "")).lower().endswith(".pdf"):
                        continue
                    url = item.get("fileurl")
                    if not url:
                        continue
                    parsed = urllib.parse.urlsplit(url)
                    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                    query = [(key, value) for key, value in query if key != "token"]
                    query.append(("token", token))
                    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
                    origin = item.get("filepathname") or item.get("filename") or lesson
                    old = known.get(origin)
                    target_dir = section_dir / safe_name(lesson, "resource")
                    target_dir.mkdir(parents=True, exist_ok=True)
                    filename = safe_name(item.get("filename", "material.pdf"), "material.pdf")
                    target = target_dir / filename
                    # Existing files are reused only when their hash matches the manifest.
                    if old and target.is_file() and target.stat().st_size == old.get("size"):
                        digest = hashlib.sha256(target.read_bytes()).hexdigest()
                        if digest == old.get("sha256"):
                            skipped += 1
                            continue
                    try:
                        data, content_type = fetch(url)
                    except (urllib.error.URLError, TimeoutError, OSError) as exc:
                        raise RuntimeError(f"Download failed for section {section.get('section')}, resource {safe_name(lesson)} ({type(exc).__name__})") from None
                    if not data.startswith(b"%PDF-"):
                        raise RuntimeError(f"Moodle item was not a PDF: section {section.get('section')}, {safe_name(lesson)}")
                    digest = hashlib.sha256(data).hexdigest()
                    target.write_bytes(data)
                    entry = {
                        "section": section.get("section"),
                        "section_title": section_title,
                        "lesson": lesson,
                        "file": str(target.relative_to(output)),
                        "origin": origin,
                        "size": len(data),
                        "sha256": digest,
                    }
                    if content_type:
                        entry["content_type"] = content_type.split(";", 1)[0]
                    if old:
                        manifest["files"][manifest["files"].index(old)] = entry
                    else:
                        manifest["files"].append(entry)
                        known[origin] = entry
                    downloaded += 1
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(manifest_path)
        print(f"Scaricati: {downloaded}; riutilizzati: {skipped}; manifest: {manifest_path}")
        return 0
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
        # Do not print exception strings from network calls; URLs may carry authorization tokens.
        message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
        print(f"Import Moodle non completato: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
