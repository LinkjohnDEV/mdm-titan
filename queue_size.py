#!/usr/bin/env python3
"""Estima espaço total da fila atual via GET stream (paralelo)."""
import os, sys, requests
import psycopg2.extras
from db import get_connection
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}

def get_size(url):
    """Retorna tamanho via GET stream — fecha conexão antes de baixar body."""
    try:
        r = requests.get(url, headers=HEADERS, allow_redirects=True, stream=True, timeout=15)
        try:
            cl = r.headers.get("Content-Length")
            if cl and int(cl) > 0:
                return int(cl)
        finally:
            r.close()
    except Exception:
        pass
    return None

def human(n):
    for u in ["B","KB","MB","GB","TB"]:
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def main():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, series_name, tipo, temporada, caminho
        FROM jobs WHERE status IN ('queued','downloading')
        ORDER BY id
    """)
    jobs = cur.fetchall()
    cur.close(); conn.close()

    tasks = []
    job_meta = []
    for idx, job in enumerate(jobs):
        links_file = os.path.join(job["caminho"], "links.txt")
        urls = []
        try:
            with open(links_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    url = line.split("|", 1)[1] if "|" in line else line
                    urls.append(url.strip())
        except Exception:
            pass
        job_meta.append({
            "id": job["id"], "name": job["series_name"], "tipo": job["tipo"],
            "temp": job.get("temporada"), "total": 0, "unknown": 0
        })
        for u in urls:
            tasks.append((idx, u))

    print(f"Sondando {len(tasks)} URL(s) em {len(jobs)} job(s)...\n")
    grand_total = 0; unknown_total = 0
    done = 0

    with ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(get_size, url): (idx, url) for idx, url in tasks}
        for fut in as_completed(futures):
            idx, url = futures[fut]
            done += 1
            if done % 20 == 0:
                print(f"  ... {done}/{len(tasks)}", file=sys.stderr, flush=True)
            try:
                size = fut.result()
            except Exception:
                size = None
            if size:
                job_meta[idx]["total"] += size
                grand_total += size
            else:
                job_meta[idx]["unknown"] += 1
                unknown_total += 1

    print(f"\n{'ID':>5}  {'TIPO':<6}  {'TAMANHO':>11}  CONTEÚDO")
    print("-" * 80)
    for m in job_meta:
        label = f"{m['name']}"
        if m['tipo'] == 'serie' and m['temp']:
            label += f" T{m['temp']}"
        size_str = human(m["total"]) if m["total"] else "?"
        if m["unknown"]:
            size_str += f" (+{m['unknown']}?)"
        print(f"{m['id']:>5}  {m['tipo']:<6}  {size_str:>11}  {label[:60]}")

    print("-" * 80)
    print(f"\nTOTAL conhecido:    {human(grand_total)}")
    if unknown_total:
        print(f"URLs sem tamanho:   {unknown_total}")

    import shutil
    print("\nEspaço livre nos storages:")
    for pool in ["/mnt/media", "/mnt/media2", "/mnt/media3"]:
        try:
            total, used, free = shutil.disk_usage(pool)
            print(f"  {pool:<15}  {human(free):>10}  livre  ({human(total)} total)")
        except OSError:
            pass

if __name__ == "__main__":
    main()
