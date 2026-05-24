#!/usr/bin/env python3
"""
Rastreia duplicatas em /mnt/media{,2,3}/filmes e /series.
Modo padrão: só REPORTA. Passar --delete pra apagar de fato (keep = maior tamanho).
"""
import os
import re
import sys
import shutil
from collections import defaultdict

POOLS = ["/mnt/media", "/mnt/media2", "/mnt/media3"]
CATEGORIES = ["filmes", "series"]

_QUALITY_TAGS_RX = re.compile(
    r'\b(1080p|720p|480p|2160p|4k|hdr|web-?dl|web-?rip|bluray|bdrip|hdtv|hdrip|x264|x265|hevc|avc|aac|dts|ddp5\.?1)\b',
    re.IGNORECASE,
)
_YEAR_RX = re.compile(r'\b(19|20)\d{2}\b')

def normalize_title(name):
    if not name:
        return ''
    name = re.sub(r'\([^)]*\)', '', name)
    name = re.sub(r'\[[^\]]*\]', '', name)
    name = _YEAR_RX.sub('', name)
    name = _QUALITY_TAGS_RX.sub('', name)
    name = re.sub(r'[._\-]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name.lower()

def folder_size(path):
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total

def human(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def scan_category(category):
    """Retorna {normalized_title: [(path, size, original_name), ...]}"""
    groups = defaultdict(list)
    for pool in POOLS:
        category_path = os.path.join(pool, category)
        if not os.path.isdir(category_path):
            continue
        try:
            for entry in os.scandir(category_path):
                if not entry.is_dir():
                    continue
                norm = normalize_title(entry.name)
                if not norm:
                    continue
                size = folder_size(entry.path)
                groups[norm].append((entry.path, size, entry.name))
        except (PermissionError, FileNotFoundError):
            pass
    return groups

def main():
    dry_run = "--delete" not in sys.argv

    print("=" * 80)
    print("RELATORIO DE DUPLICATAS — modo:", "DELETE" if not dry_run else "SCAN (dry-run)")
    print("=" * 80)

    total_recoverable = 0
    total_groups = 0
    to_delete = []

    for category in CATEGORIES:
        print(f"\n### /{category} ###")
        groups = scan_category(category)
        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dup_groups:
            print("  (sem duplicatas)")
            continue

        for norm_name, entries in sorted(dup_groups.items()):
            total_groups += 1
            # ordena por tamanho desc; manter o maior
            entries.sort(key=lambda x: x[1], reverse=True)
            keeper = entries[0]
            losers = entries[1:]
            print(f"\n  [{norm_name}]")
            print(f"    KEEP  → {human(keeper[1]):>10}  {keeper[0]}")
            for loser in losers:
                print(f"    DEL   → {human(loser[1]):>10}  {loser[0]}")
                total_recoverable += loser[1]
                to_delete.append(loser[0])

    print("\n" + "=" * 80)
    print(f"Total: {total_groups} grupo(s) com duplicatas")
    print(f"Recuperável: {human(total_recoverable)}")
    print("=" * 80)

    if dry_run:
        print("\nDry-run — nada foi apagado. Para apagar de fato:")
        print("  python3 /opt/mdm/scan_dups.py --delete")
        return

    if not to_delete:
        return

    print(f"\nApagando {len(to_delete)} pasta(s)...")
    deleted = 0
    errors = 0
    for path in to_delete:
        try:
            shutil.rmtree(path)
            deleted += 1
            print(f"  ✓ {path}")
        except Exception as e:
            errors += 1
            print(f"  ✗ {path} — {e}")

    print(f"\nApagado: {deleted}  Erros: {errors}")
    print(f"Espaço recuperado: ~{human(total_recoverable)}")

if __name__ == "__main__":
    main()
