"""
MDM Indexer — Indexa dados.txt (M3U) em SQLite in-memory para busca rápida.
Detecta automaticamente quando o arquivo muda e reindexa.
"""

import os
import sqlite3
import time
import threading

# =========================================================
# ====================== CONFIGURAÇÕES ====================
# =========================================================

_db = None              # Conexão SQLite in-memory
_lock = threading.Lock()
_last_mtime = 0         # Última modificação do arquivo indexado
_last_size = 0          # Último tamanho do arquivo
_data_file = None       # Caminho do dados.txt
_stats = {"filmes": 0, "series": 0, "total": 0, "index_time": 0}


# =========================================================
# ======================== INIT ===========================
# =========================================================

def init(data_file):
    """Inicializa o indexer com o caminho do dados.txt"""
    global _data_file
    _data_file = data_file
    _build_index()


def _needs_reindex():
    """Verifica se o arquivo mudou desde a última indexação"""
    global _last_mtime, _last_size
    if not _data_file or not os.path.exists(_data_file):
        return False
    stat = os.stat(_data_file)
    return stat.st_mtime != _last_mtime or stat.st_size != _last_size


def _ensure_index():
    """Garante que o índice está atualizado"""
    if _db is None or _needs_reindex():
        _build_index()


def _build_index():
    """Parseia o M3U e cria o índice SQLite in-memory"""
    global _db, _last_mtime, _last_size, _stats

    if not _data_file or not os.path.exists(_data_file):
        print("[indexer] ⚠️  Arquivo não encontrado:", _data_file)
        return

    start = time.time()
    stat = os.stat(_data_file)

    print(f"[indexer] 🔄 Indexando {_data_file} ({stat.st_size / (1024*1024):.0f} MB)...")

    # Cria novo banco in-memory
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("""
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT,
            nome TEXT,
            nome_lower TEXT,
            url TEXT,
            group_title TEXT,
            logo TEXT
        )
    """)
    db.execute("CREATE INDEX idx_tipo ON entries(tipo)")
    db.execute("CREATE INDEX idx_nome_lower ON entries(nome_lower)")

    filmes = 0
    series = 0
    batch = []
    batch_size = 5000

    with open(_data_file, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    total_lines = len(lines)

    while i < total_lines:
        line = lines[i].strip()

        if line.startswith("#EXTINF"):
            # Extrai metadados da linha #EXTINF
            nome = line.split(",")[-1].strip() if "," in line else ""
            group_title = ""
            logo = ""

            # Extrai group-title
            if 'group-title="' in line:
                try:
                    group_title = line.split('group-title="')[1].split('"')[0]
                except (IndexError, ValueError):
                    pass

            # Extrai logo
            if 'tvg-logo="' in line:
                try:
                    logo = line.split('tvg-logo="')[1].split('"')[0]
                except (IndexError, ValueError):
                    pass

            # Próxima linha = URL
            url = ""
            if i + 1 < total_lines:
                next_line = lines[i + 1].strip()
                if next_line.startswith("http"):
                    url = next_line
                    i += 1  # Pula a linha da URL

            # Classifica o tipo
            if "/movie/" in url:
                tipo = "filme"
                filmes += 1
            elif "/series/" in url:
                tipo = "serie"
                series += 1
            else:
                # Classifica pelo group-title como fallback
                gt_upper = group_title.upper()
                if "FILME" in gt_upper and "SÉRIE" not in gt_upper and "SERIES" not in gt_upper:
                    tipo = "filme"
                    filmes += 1
                elif "SÉRIE" in gt_upper or "SERIES" in gt_upper:
                    tipo = "serie"
                    series += 1
                else:
                    tipo = "canal"

            if nome and url:
                batch.append((tipo, nome, nome.lower(), url, group_title, logo))

                if len(batch) >= batch_size:
                    db.executemany(
                        "INSERT INTO entries (tipo, nome, nome_lower, url, group_title, logo) VALUES (?,?,?,?,?,?)",
                        batch
                    )
                    batch = []

        i += 1

    # Insere batch restante
    if batch:
        db.executemany(
            "INSERT INTO entries (tipo, nome, nome_lower, url, group_title, logo) VALUES (?,?,?,?,?,?)",
            batch
        )

    db.commit()

    elapsed = time.time() - start

    with _lock:
        old_db = _db
        _db = db
        _last_mtime = stat.st_mtime
        _last_size = stat.st_size
        _stats = {
            "filmes": filmes,
            "series": series,
            "total": filmes + series,
            "index_time": round(elapsed, 2)
        }

    # Fecha banco anterior se existia
    if old_db:
        try:
            old_db.close()
        except Exception:
            pass

    print(f"[indexer] ✅ Indexado em {elapsed:.1f}s — {filmes} filmes, {series} séries")


# =========================================================
# ======================== BUSCA ==========================
# =========================================================

def search_filmes(query, limit=100):
    """Busca filmes pelo nome"""
    _ensure_index()
    if not _db:
        return []

    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title FROM entries
            WHERE tipo='filme' AND nome_lower LIKE ?
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"nome": r[0], "link": r[1], "logo": r[2], "group": r[3]} for r in cursor.fetchall()]


def search_series(query, limit=50):
    """Busca séries pelo nome"""
    _ensure_index()
    if not _db:
        return []

    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title FROM entries
            WHERE tipo='serie' AND nome_lower LIKE ?
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"name": r[0], "url": r[1], "logo": r[2], "group": r[3]} for r in cursor.fetchall()]


def search_desenhos_filmes(query, limit=100):
    """Busca filmes para aba Desenhos (mesma lógica de search_filmes)"""
    _ensure_index()
    if not _db:
        return []
    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title FROM entries
            WHERE tipo='filme' AND nome_lower LIKE ?
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"nome": r[0], "link": r[1], "logo": r[2], "group": r[3]} for r in cursor.fetchall()]


def search_desenhos_series(query, limit=50):
    """Busca séries para aba Desenhos (mesma lógica de search_series)"""
    _ensure_index()
    if not _db:
        return []
    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title FROM entries
            WHERE tipo='serie' AND nome_lower LIKE ?
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"name": r[0], "url": r[1], "logo": r[2], "group": r[3]} for r in cursor.fetchall()]


def search_animes_filmes(query, limit=100):
    """Busca filmes para aba Animes (mesma lógica de search_filmes)"""
    _ensure_index()
    if not _db:
        return []
    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title FROM entries
            WHERE tipo='filme' AND nome_lower LIKE ?
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"nome": r[0], "link": r[1], "logo": r[2], "group": r[3]} for r in cursor.fetchall()]


def search_animes_series(query, limit=50):
    """Busca séries para aba Animes (mesma lógica de search_series)"""
    _ensure_index()
    if not _db:
        return []
    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title FROM entries
            WHERE tipo='serie' AND nome_lower LIKE ?
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"name": r[0], "url": r[1], "logo": r[2], "group": r[3]} for r in cursor.fetchall()]


def search_all(query, limit=100):
    """Busca em todos os tipos (filmes + séries) — usado pelo Coringa Download."""
    _ensure_index()
    if not _db:
        return []
    with _lock:
        cursor = _db.execute("""
            SELECT nome, url, logo, group_title, tipo FROM entries
            WHERE nome_lower LIKE ?
            ORDER BY tipo, nome_lower
            LIMIT ?
        """, (f"%{query.lower()}%", limit))
        return [{"nome": r[0], "link": r[1], "logo": r[2], "group": r[3], "tipo": r[4]} for r in cursor.fetchall()]


def reindex():
    """Força re-indexação manual"""
    _build_index()
    return _stats


def get_stats():
    """Retorna estatísticas do índice"""
    return _stats
