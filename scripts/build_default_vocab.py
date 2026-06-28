#!/usr/bin/env python3
"""Build Argus's curated default keyword vocabulary (~8k) from Open Images + phrases.

The shipped default (app/db/default_vocabulary.txt) must tag general photos well while
staying small enough to build/score cheaply (esp. on CPU/Apple). The full Open Images
V7 name set (~20k) is mostly long-tail noise — obscure species, product/brand names,
sports events ("110 metres hurdles", "1937 ford", "1800 tequila"). This script keeps:

  - the most COMMON Open Images terms, ranked by English word frequency (wordfreq), and
  - the full curated phrase supplement (occasions/moods/scenes) verbatim — those are
    intentional and never frequency-filtered.

Junk filter for Open Images names: drop anything containing digits, longer than 4 words,
or with no reasonably common word in it. Then sort the rest by mean word frequency and
take enough to reach the target total once the phrase supplement is added.

    pip install wordfreq
    python scripts/build_default_vocab.py --target 8000

Writes app/db/default_vocabulary.txt. Re-run with a different --target to resize.
The phrase supplement is recovered as (current default) - (Open Images names), so the
curated phrases already in the file are preserved across re-runs.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import urllib.request
from pathlib import Path

OID_URL = "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions.csv"
# Brysbaert et al. concreteness norms (~40k English words, rated 1-5). Used to drop
# abstract words ("attribute", "international") and proper nouns/place names ("Bronx",
# which simply aren't in the norms) that frequency ranking can't catch.
CONC_URL = ("https://raw.githubusercontent.com/ArtsEngine/concreteness/master/"
            "Concreteness_ratings_Brysbaert_et_al_BRM.txt")
DEFAULT_FILE = Path(__file__).resolve().parent.parent / "app" / "db" / "default_vocabulary.txt"

# Always keep these regardless of frequency (tests + obviously-useful anchors).
MUST_KEEP = {"christmas", "birthday party"}

_STOP = {"a", "an", "the", "of", "and", "or", "with", "in", "on", "de", "la", "le"}

# Brand tokens that pass concreteness/frequency but are useless as photo tags. Any term
# containing one of these is dropped (e.g. "Ford edge", "Dodge charger"). Generic words
# like "car"/"truck"/"sedan" stay; only the maker/model noise goes.
_BRAND_BLOCK = {
    "ford", "chevrolet", "chevy", "toyota", "honda", "nissan", "mazda", "daihatsu",
    "holden", "buick", "volkswagen", "audi", "bmw", "mercedes", "suzuki", "subaru",
    "kia", "hyundai", "dodge", "jeep", "lexus", "porsche", "ferrari", "tesla",
    "renault", "peugeot", "fiat", "volvo", "cadillac", "chrysler", "mitsubishi",
    "bentley", "jaguar", "maserati", "lamborghini", "bugatti", "acura", "infiniti",
    "datsun", "pontiac", "oldsmobile", "plymouth", "saab", "skoda", "opel", "lincoln",
    "rover", "mini", "isuzu", "lada", "citroen", "seat", "genesis",
}


def _blocked(name: str) -> bool:
    return any(t.lower() in _BRAND_BLOCK for t in re.split(r"[\s/\-]+", name))


def fetch_oid_names() -> list[str]:
    print(f"downloading {OID_URL} ...")
    with urllib.request.urlopen(OID_URL, timeout=120) as resp:
        text = resp.read().decode("utf-8")
    names: list[str] = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header: LabelName, DisplayName
    for row in reader:
        if len(row) >= 2 and row[1].strip():
            names.append(row[1].strip())
    return names


def fetch_concreteness() -> dict[str, float]:
    print(f"downloading {CONC_URL} ...")
    with urllib.request.urlopen(CONC_URL, timeout=120) as resp:
        text = resp.read().decode("utf-8", "replace")
    conc: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        try:
            conc[row["Word"].lower()] = float(row["Conc.M"])
        except (KeyError, ValueError):
            pass
    return conc


def is_concrete(name: str, conc: dict[str, float], cmin: float) -> bool:
    """True when every content word is a known, sufficiently concrete word. Unknown
    words (proper nouns/place names like 'Bronx') and abstract words ('attribute') fail."""
    tokens = [t for t in re.split(r"[\s/\-]+", name.lower()) if t and t not in _STOP and len(t) >= 3]
    if not tokens:
        return False
    return all(conc.get(t, 0.0) >= cmin for t in tokens)


def score(name: str, zipf, min_token: float) -> float:
    """Mean word-frequency (Zipf 0-8) of a name's content words; 0.0 if junk/obscure.

    Open Images is full of multi-word brand/product/niche names whose words are each
    common in isolation ("burger king premium burgers", "sega game gear"). To kill
    those we cap at two words AND require EVERY content word to clear min_token, so a
    single rare/brand word sinks the whole term.
    """
    if any(ch.isdigit() for ch in name):
        return 0.0
    tokens = [t for t in re.split(r"[\s/\-]+", name.lower()) if t and t not in _STOP]
    if not tokens or len(tokens) > 2:
        return 0.0
    freqs = [zipf(t, "en") for t in tokens if len(t) >= 3]
    if not freqs or min(freqs) < min_token:
        return 0.0
    return sum(freqs) / len(freqs)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Argus's curated default vocabulary")
    ap.add_argument("--target", type=int, default=8000, help="approx total entries (default 8000)")
    ap.add_argument("--out", default=str(DEFAULT_FILE), help="output file")
    ap.add_argument("--min-zipf", type=float, default=2.5,
                    help="drop Open Images terms below this mean word frequency")
    ap.add_argument("--min-token", type=float, default=3.0,
                    help="drop terms with any content word below this frequency (kills brand junk)")
    ap.add_argument("--concreteness-min", type=float, default=3.5,
                    help="drop terms with any word below this concreteness (kills abstract/proper nouns)")
    args = ap.parse_args()

    from wordfreq import zipf_frequency as zipf

    oid = fetch_oid_names()
    oid_lower = {w.lower() for w in oid}
    conc = fetch_concreteness()

    # Recover the curated phrase supplement: whatever is in the current default but not
    # an Open Images name (these are the hand-authored occasion/mood/scene phrases).
    supplement: list[str] = []
    if Path(args.out).exists():
        for ln in Path(args.out).read_text(encoding="utf-8").splitlines():
            w = ln.strip()
            if w and not w.startswith("#") and w.lower() not in oid_lower:
                supplement.append(w)
    print(f"recovered {len(supplement)} curated phrases from existing default")

    # Keep only concrete, common Open Images names, then rank by commonness.
    scored = [(w, score(w, zipf, args.min_token)) for w in oid
              if is_concrete(w, conc, args.concreteness_min) and not _blocked(w)]
    scored = [(w, s) for w, s in scored if s >= args.min_zipf]
    scored.sort(key=lambda t: t[1], reverse=True)

    need = max(0, args.target - len(supplement))
    top_oid = [w for w, _ in scored[:need]]

    # Merge: supplement first (priority), then top Open Images terms, dedup case-insensitively.
    out: list[str] = []
    seen: set[str] = set()
    for w in supplement + top_oid:
        k = w.lower()
        if k not in seen:
            seen.add(k)
            out.append(w)

    # Guarantee anchors survive.
    for w in MUST_KEEP:
        if w not in seen:
            out.append(w)
            seen.add(w)

    out.sort(key=str.lower)
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} entries to {args.out} "
          f"({len(supplement)} phrases + {len(out) - len(supplement)} Open Images terms)")


if __name__ == "__main__":
    main()
