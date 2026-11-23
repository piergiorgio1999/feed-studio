#!/usr/bin/env python3
"""Download completed NotebookLM video artifacts absent from local downloads."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path.home() / ".nbvideo"
STATE = ROOT / "state.json"
DOWNLOADS = ROOT / "downloads"


def cli(profile, *args):
    executable = shutil.which("notebooklm") or str(Path.home() / ".local/bin/notebooklm")
    return subprocess.run(
        [executable, "--profile", profile, *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def read_state():
    try:
        state = json.loads(STATE.read_text())
    except FileNotFoundError:
        raise RuntimeError(f"State non trovato: {STATE}")
    if not isinstance(state, dict) or not isinstance(state.get("accounts"), dict):
        raise RuntimeError(f"Formato accounts non valido in {STATE}")
    return state


def records(payload):
    """Return artifact dictionaries from common notebooklm-py JSON envelopes."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("artifacts", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = records(value)
                if nested:
                    return nested
        # Some versions return a single artifact object.
        if any(key in payload for key in ("artifact_id", "id", "task_id")):
            return [payload]
    raise ValueError("Formato JSON inatteso: attesa lista artifact o oggetto con artifacts/items/results/data")


def artifact_id(row):
    for key in ("artifact_id", "id", "task_id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def is_complete(row):
    status = str(row.get("status", row.get("state", ""))).strip().lower()
    return status in {"completed", "complete", "ready", "succeeded", "success"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downloads", type=Path, default=DOWNLOADS,
                        help=f"Cartella dei file MP4 (default: {DOWNLOADS})")
    args = parser.parse_args()
    state = read_state()
    downloaded = 0
    failures = 0

    for account, info in state["accounts"].items():
        if not isinstance(info, dict) or not isinstance(info.get("notebooks"), dict):
            print(f"{account}: mappa notebooks assente o non valida", file=sys.stderr)
            failures += 1
            continue
        for notebook_name, notebook_id in info["notebooks"].items():
            try:
                raw = cli(account, "artifact", "list", "--type", "video", "--json",
                          "-n", str(notebook_id))
                artifacts = records(json.loads(raw))
            except (subprocess.CalledProcessError, OSError, ValueError) as exc:
                detail = getattr(exc, "stderr", None) or str(exc)
                print(f"{account}/{notebook_name}: elenco artifact fallito: {detail}", file=sys.stderr)
                failures += 1
                continue

            for row in artifacts:
                aid = artifact_id(row)
                if not aid:
                    print(f"{account}/{notebook_name}: artifact senza ID: {row}", file=sys.stderr)
                    failures += 1
                    continue
                if not is_complete(row):
                    continue
                # Include account and notebook to avoid collisions across profiles/quaderni.
                dest = args.downloads / account / str(notebook_name) / f"{aid}.mp4"
                if dest.is_file() and dest.stat().st_size > 0:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    cli(account, "download", "video", str(dest), "--notebook", str(notebook_id),
                        "--artifact", aid, "--no-clobber")
                    if not dest.is_file() or dest.stat().st_size == 0:
                        raise RuntimeError("Download MP4 assente o vuoto")
                    downloaded += 1
                    print(f"Scaricato {account}/{notebook_name}/{aid}.mp4")
                except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
                    detail = getattr(exc, "stderr", None) or str(exc)
                    print(f"{account}/{notebook_name}/{aid}: download fallito: {detail}", file=sys.stderr)
                    failures += 1

    print(f"Nuovi MP4: {downloaded}; errori: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
