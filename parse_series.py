#!/usr/bin/env python3
"""
Parse M3U file and extract episodes for specific series into JSON.
"""

import re
import json

INPUT_FILE = "/opt/mdm/data/dados.txt"
OUTPUT_FILE = "/opt/mdm/data/series.json"

# Series definitions: canonical name -> list of (pattern, required_group_pattern or None)
# required_group_pattern: if set, the group-title must also match this regex (case-insensitive)
# Order matters: more specific patterns first to avoid false matches.
SERIES_PATTERNS = [
    ("Breaking Bad",        [(r"breaking bad", None)]),
    ("Stranger Things",     [(r"stranger things", None)]),
    ("Better Call Saul",    [(r"better call saul", None)]),
    # "Dark" — exact start match: "Dark SxxExx" only (avoids "Home Before Dark", "Into the Dark", etc.)
    ("Dark",                [(r"^dark\s+s\d", None)]),
    ("The Crown",           [(r"^the crown\s+s\d", None)]),
    ("Peaky Blinders",      [(r"peaky blinders", None)]),
    ("Ozark",               [(r"^ozark\s+s\d", None)]),
    ("Mindhunter",          [(r"mindhunter", None)]),
    ("Black Mirror",        [(r"^black mirror", None)]),
    # One Piece Live Action: "ONE PIECE A Série SxxExx" (Netflix live-action title)
    # Do NOT match plain "One Piece SxxExx" — that's the anime (even in NETFLIX group)
    ("One Piece (Live Action)", [
        (r"one piece\s+a\s+s", None),
        (r"one piece.*série", None),
        (r"one piece.*live", None),
    ]),
    ("The Witcher",         [(r"^the witcher\s+s\d", None)]),
    ("Cobra Kai",           [(r"cobra kai", None)]),
    # Round 6 / Squid Game are the same show
    ("Round 6 / Squid Game", [(r"squid game", None), (r"^round\s*6\b", None)]),
    ("Narcos",              [(r"^narcos\b", None)]),
    ("House of Cards",      [(r"house of cards", None)]),
    ("La Casa de Papel",    [(r"la casa de papel", None)]),
    ("BoJack Horseman",     [(r"bojack horseman", None)]),
    ("The Sandman",         [(r"^the sandman\s+s\d", None)]),
    ("Sex Education",       [(r"sex education", None)]),
    ("Lucifer",             [(r"^lucifer\s+s\d", None)]),
    # "You" — the Netflix series is stored as "Você" in Portuguese, or "You SxxExx" in English
    ("You",                 [
        (r"^you\s+s\d", None),
        (r"^you\s+\(\d{4}", None),
        (r"^você\s+s\d", None),
    ]),
    ("The Umbrella Academy",[(r"umbrella academy", None)]),
    # "Elite" — series format only (avoids "Força de Elite", "Esquadrão de Elite", etc.)
    ("Elite",               [(r"^elite\s+s\d", None), (r"^elite\s+0\d\b", None)]),
    ("Heartstopper",        [(r"heartstopper", None)]),
    ("Bridgerton",          [(r"bridgerton", None)]),
    ("O Gambito da Rainha / Queen's Gambit", [
        (r"queen.s gambit", None),
        (r"gambito da rainha", None),
    ]),
    ("Missa da Meia-Noite / Midnight Mass", [
        (r"midnight mass", None),
        (r"missa da meia.noite", None),
    ]),
    ("Dele & Dela",         [(r"^dele\s*[&e]\s*dela\b", None)]),
    ("O Poder e a Lei",     [(r"^o poder e a lei\s+s\d", None)]),
    ("Sweet Tooth",         [(r"sweet tooth", None)]),
    ("Emily em Paris / Emily in Paris", [
        (r"emily (em|in) paris", None),
    ]),
    ("Manifest",            [(r"^manifest\s+s\d", None)]),
    ("Lupin",               [(r"^lupin\s+s\d", None)]),
]

# Compile all patterns
COMPILED_PATTERNS = [
    (name, [(re.compile(pat, re.IGNORECASE),
             re.compile(grp, re.IGNORECASE) if grp else None)
            for pat, grp in pat_list])
    for name, pat_list in SERIES_PATTERNS
]


def match_series(tvg_name, group_title):
    """Return the canonical series name if tvg_name (and optionally group) match."""
    for series_name, patterns in COMPILED_PATTERNS:
        for name_pat, grp_pat in patterns:
            if name_pat.search(tvg_name):
                if grp_pat is None or grp_pat.search(group_title):
                    return series_name
    return None


def parse_extinf(line):
    """Parse an #EXTINF line and return (tvg_name, tvg_logo, group_title, display_name)."""
    tvg_name = ""
    tvg_logo = ""
    group_title = ""

    m = re.search(r'tvg-name="([^"]*)"', line)
    if m:
        tvg_name = m.group(1)

    m = re.search(r'tvg-logo="([^"]*)"', line)
    if m:
        tvg_logo = m.group(1)

    m = re.search(r'group-title="([^"]*)"', line)
    if m:
        group_title = m.group(1)

    # Display name is after the last comma
    comma_pos = line.rfind(",")
    display_name = line[comma_pos + 1:].strip() if comma_pos != -1 else tvg_name

    return tvg_name, tvg_logo, group_title, display_name


def main():
    # episodes_map: series_name -> list of episode dicts
    episodes_map = {name: [] for name, _ in SERIES_PATTERNS}
    # seen_tvg_names: series_name -> set of tvg_names already added (for dedup)
    seen_tvg_names = {name: set() for name, _ in SERIES_PATTERNS}

    with open(INPUT_FILE, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("#EXTINF:"):
            tvg_name, tvg_logo, group_title, display_name = parse_extinf(line)

            # Get URL (next non-empty, non-comment line)
            url = ""
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("#"):
                    url = next_line
                    break
                j += 1

            # Match to a series
            series = match_series(tvg_name, group_title)
            if series and tvg_name:
                if tvg_name not in seen_tvg_names[series]:
                    seen_tvg_names[series].add(tvg_name)
                    episodes_map[series].append({
                        "name": display_name,
                        "logo": tvg_logo,
                        "group": group_title,
                        "url": url,
                    })
        i += 1

    # Build output structure preserving original series order
    series_list = []
    for series_name, _ in SERIES_PATTERNS:
        eps = episodes_map[series_name]
        if eps:
            series_list.append({
                "serie": series_name,
                "episodes": eps,
            })

    output = {"series": series_list}

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Summary
    total_eps = sum(len(s["episodes"]) for s in series_list)
    print(f"Done. {len(series_list)} series found, {total_eps} total episodes.")
    for s in series_list:
        print(f"  {s['serie']}: {len(s['episodes'])} episodes")


if __name__ == "__main__":
    main()
