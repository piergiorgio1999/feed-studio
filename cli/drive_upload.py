#!/usr/bin/env python3
"""Upload NotebookLM MP4 files to Google Drive through a configured rclone remote."""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = "Gemini Notebook Video"


def folder_name(value: str) -> str:
    """Keep notebook names as one safe path component."""
    name = re.sub(r"[/\\\x00-\x1f]", "_", value).strip().strip(".")
    if not name:
        raise ValueError("Il nome del quaderno non può essere vuoto")
    return name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Carica un MP4 su Google Drive con rclone, senza duplicarlo se invariato."
    )
    parser.add_argument("file", type=Path, help="File MP4 da caricare")
    parser.add_argument("--notebook", required=True, help="Nome del quaderno; diventa una sottocartella")
    parser.add_argument("--remote", required=True, help="Nome del remote rclone, per esempio gdrive")
    parser.add_argument("--root", default=DEFAULT_ROOT, help=f"Cartella radice Drive (default: {DEFAULT_ROOT})")
    args = parser.parse_args()

    source = args.file.expanduser().resolve()
    if not source.is_file():
        parser.error(f"File non trovato: {source}")
    if source.suffix.lower() != ".mp4":
        parser.error("Sono accettati solo file .mp4")
    if not shutil.which("rclone"):
        parser.error("rclone non trovato; installalo e configura prima un remote Google Drive")
    remote = args.remote.removesuffix(":")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", remote):
        parser.error("--remote deve essere il nome del remote rclone, senza percorso")
    try:
        notebook = folder_name(args.notebook)
        root = folder_name(args.root)
    except ValueError as exc:
        parser.error(str(exc))

    destination = f"{remote}:{root}/{notebook}/{source.name}"
    try:
        subprocess.run(
            ["rclone", "copyto", str(source), destination, "--checksum"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Upload rclone fallito (codice {exc.returncode}): {destination}", file=sys.stderr)
        return exc.returncode or 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
