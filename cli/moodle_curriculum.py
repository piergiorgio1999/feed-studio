#!/usr/bin/env python3
"""Generate a page-grounded level-2 syllabus for Morpurgo's Moodle PDFs."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_SYLLABUS = ROOT / "syllabi" / "morpurgo-moodle-7633.jsonl"
DEFAULT_MANIFEST = Path.home() / ".nbvideo" / "moodle" / "7633" / "manifest.json"


def read_jsonl(path):
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON non valido in {path}:{number}") from exc
    return rows


def pdf_pages(path):
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)], check=True, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("pdfinfo non trovato: installa poppler") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"pdfinfo non riesce a leggere {path.name}") from exc
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    if not match:
        raise RuntimeError(f"numero pagine non disponibile per {path.name}")
    return int(match.group(1))


def generate(syllabus_path, manifest_path):
    syllabus = read_jsonl(syllabus_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"manifest illeggibile: {manifest_path}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeError("formato manifest inatteso")

    by_filename = {}
    for entry in manifest["files"]:
        if isinstance(entry, dict) and entry.get("file"):
            by_filename[Path(entry["file"]).name.casefold()] = entry

    rows = []
    lessons = [row for row in syllabus if row.get("level") == 1 and "dispensa Moodle" in row.get("prompt", "")]
    if len(lessons) != 41:
        raise RuntimeError(f"attese 41 lezioni con PDF, trovate {len(lessons)}")
    for lesson in lessons:
        match = re.search(r"dispensa Moodle\s+([^\s.]+\.pdf)", lesson["prompt"], re.IGNORECASE)
        if not match:
            raise RuntimeError(f"nome PDF assente nel prompt: {lesson['topic']}")
        filename = match.group(1)
        entry = by_filename.get(filename.casefold())
        if not entry:
            raise RuntimeError(f"PDF assente dal manifest: {filename}")
        pdf = manifest_path.parent / entry["file"]
        if not pdf.is_file():
            raise RuntimeError(f"PDF locale assente: {entry['file']}")
        pages = pdf_pages(pdf)
        for page in range(1, pages + 1):
            source = f"dispensa Moodle {filename}, pagina {page} di {pages}"
            for kind, label, task in (
                ("focus", "spiegazione focalizzata", "Crea uno Short didattico conciso sui contenuti leggibili in questa pagina: definizioni, passaggi, formule, figure ed esempi solo se presenti. Collega i passaggi senza aggiungere fatti non supportati dalla pagina."),
                ("orale", "orale e applicazione", "Crea uno Short di domanda e risposta o applicazione basato esclusivamente su questa pagina; svolgi la risposta passo per passo usando soltanto dati e relazioni che vi compaiono. Se la pagina non consente un'applicazione, chiarisci il limite senza inventare un esempio."),
            ):
                rows.append({
                    "topic": f"{lesson['topic']} — p. {page}: {label}",
                    "level": 2,
                    "parent": lesson["topic"],
                    "format": "short",
                    "prompt": f"Fonte precisa: {source}. {task} Distingui ciò che è esplicito da ciò che non è determinabile. Non attribuire alla pagina contenuti di altre lezioni.",
                    "meta": {"source_file": filename, "source_page": page, "source_pages": pages, "variant": kind},
                })
    return syllabus + rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="percorso JSONL da creare")
    parser.add_argument("--syllabus", type=Path, default=DEFAULT_SYLLABUS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        rows = generate(args.syllabus.expanduser(), args.manifest.expanduser())
        output = args.output.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Curriculum non generato: {exc}", file=sys.stderr)
        return 1
    print(f"Creati {len(rows)} argomenti da 41 PDF: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
