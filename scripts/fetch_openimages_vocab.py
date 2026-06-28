#!/usr/bin/env python3
"""Build the full ~20k Open Images keyword vocabulary for Argus.

The agreed default vocabulary is the Open Images image-level label names (~19.9k,
CC BY 4.0) plus a curated phrase supplement. Argus ships a smaller hand-curated
default (app/db/default_vocabulary.txt) so tagging works out of the box without a
download; this script produces the full Open Images set when you want max coverage.

    python scripts/fetch_openimages_vocab.py --out openimages_vocab.txt

Then either:
  - replace app/db/default_vocabulary.txt with the output (seeds fresh installs), or
  - upload the output on the Models page (Keyword vocabulary -> Upload) to replace the
    vocabulary on a running instance.

By default the curated phrase supplement (app/db/default_vocabulary.txt) is merged in
so theme/occasion phrases like "birthday party" are included; pass --no-supplement to
get only the Open Images names. Attribution: Open Images annotations are CC BY 4.0.
"""
from __future__ import annotations

import argparse
import csv
import io
import urllib.request
from pathlib import Path

# Open Images V7 class display names (LabelName, DisplayName).
OID_URL = "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions.csv"
SUPPLEMENT = Path(__file__).resolve().parent.parent / "app" / "db" / "default_vocabulary.txt"


def fetch_names() -> list[str]:
    print(f"downloading {OID_URL} ...")
    with urllib.request.urlopen(OID_URL, timeout=120) as resp:
        text = resp.read().decode("utf-8")
    names: list[str] = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    # The file has a header row (LabelName, DisplayName); display name is column 1.
    if header and header[0].lower() not in ("labelname", "/m/0"):
        pass  # header consumed
    for row in reader:
        if len(row) >= 2 and row[1].strip():
            names.append(row[1].strip())
    return names


def load_supplement() -> list[str]:
    if not SUPPLEMENT.exists():
        return []
    return [ln.strip() for ln in SUPPLEMENT.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Open Images keyword vocabulary")
    ap.add_argument("--out", default="openimages_vocab.txt", help="output file")
    ap.add_argument("--no-supplement", action="store_true",
                    help="exclude the curated phrase supplement")
    args = ap.parse_args()

    words = fetch_names()
    if not args.no_supplement:
        words = words + load_supplement()

    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        k = w.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(w)

    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} unique entries to {args.out}")


if __name__ == "__main__":
    main()
