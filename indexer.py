"""
MDM Indexer (multi-server) — Indexa N arquivos M3U em um SQLite in-memory.
Cada entrada carrega um server_id; buscas podem filtrar por server.
Detecta mudanças por mtime+size de cada arquivo individualmente.
"""

import os
import sqlite3
import time
import threading

# Conexão SQLite in-memory compartilhada por todos os servers
_db = None
_lock = threading.Lock()

# Caminho base do projeto (recebido em init)
_base_dir = None

# Função injetada pra carregar os servidores do Postgres
# assinatura: () -> list[{id:int, slug:str, filename:str, ativo:bool}]
_load_servers_cb = None

# Cache de estado por server_id: {server_id: {"mtime": float, "size": int, "path": str, "stats": {...}}}
_server_state = {}

# Stats globais agregadas (compat com legado)
_stats = {"filmes": 0, "series": 0, "total": 0, "index_time": 0}


# =========================================================
# ======================== INIT ===========================
# =========================================================

def init(base_dir, load_servers_callback):
    """
    Inicializa o indexer.
      base_dir: diretório raiz do projeto (pra resolver paths relativos dos arquivos)
      load_servers_callback: função zero-args que retorna lista de servers ativos
                              [{id, slug, filename, ativo, ...}, ...]
    """
    global _base_dir, _load_servers_cb
    _base_dir = base_dir
    _load_servers_cb = load_servers_callback
    _ensure_db()
    _build_all()


def _ensure_db():
    """Cria a conexão e o schema in-memory se ainda não existir."""
    global _db
    if _db is not None:
        return
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("""
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            tipo TEXT,
            nome TEXT,
            nome_lower TEXT,
            url TEXT,
            group_title TEXT,
            logo TEXT
        )
    """)
    db.execute("CREATE INDEX idx_entries_server ON entries(server_id)")
    db.execute("CREATE INDEX idx_entries_tipo ON entries(tipo)")
    db.execute("CREATE INDEX idx_entries_nome_lower ON entries(nome_lower)")
    _db = db


def _resolve_path(filename):
    if not filename:
        return None
    if os.path.isabs(filename):
        return filename
    return os.path.join(_base_dir, filename)


def _file_changed(server_id, path):
    """Verifica se o arquivo do server mudou desde a última indexação."""
    if not path or not os.path.exists(path):
        return False
    stat = os.stat(path)
    prev = _server_state.get(server_id, {})
    return stat.st_mtime != prev.get("mtime", 0) or stat.st_size != prev.get("size", 0)


def _index_one(server):
    """(Re)indexa um único server."""
    server_id = server["id"]
    slug = server.get("slug", str(server_id))
    path = _resolve_path(server["filename"])

    if not path or not os.path.exists(path):
        print(f"[indexer] ⚠️  Server '{slug}' (id={server_id}) — arquivo não encontrado: {path}")
        return {"filmes": 0, "series": 0, "total": 0, "index_time": 0}

    start = time.time()
    stat = os.stat(path)
    print(f"[indexer] 🔄 Server '{slug}' — indexando {path} ({stat.st_size / (1024*1024):.0f} MB)...")

    # Apaga entradas antigas desse server
    with _lock:
        _db.execute("DELETE FROM entries WHERE server_id=?", (server_id,))

    filmes, series = 0, 0
    batch = []
    BATCH_SIZE = 5000

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    total_lines = len(lines)
    while i < total_lines:
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            nome = line.split(",")[-1].strip() if "," in line else ""
            group_title = ""
            logo = ""
            if 'group-title="' in line:
                try: group_title = line.split('group-title="')[1].split('"')[0]
                except Exception: pass
            if 'tvg-logo="' in line:
                try: logo = line.split('tvg-logo="')[1].split('"')[0]
                except Exception: pass

            url = ""
            if i + 1 < total_lines:
                nxt = lines[i + 1].strip()
                if nxt.startswith("http"):
                    url = nxt
                    i += 1

            if "/movie/" in url:
                tipo = "filme"; filmes += 1
            elif "/series/" in url:
                tipo = "serie"; series += 1
            else:
                gt = group_title.upper()
                if "FILME" in gt and "SÉRIE" not in gt and "SERIES" not in gt:
                    tipo = "filme"; filmes += 1
                elif "SÉRIE" in gt or "SERIES" in gt:
                    tipo = "serie"; series += 1
                else:
                    tipo = "canal"

            if nome and url:
                batch.append((server_id, tipo, nome, nome.lower(), url, group_title, logo))
                if len(batch) >= BATCH_SIZE:
                    with _lock:
                        _db.executemany(
                            "INSERT INTO entries (server_id, tipo, nome, nome_lower, url, group_title, logo) VALUES (?,?,?,?,?,?,?)",
                            batch,
                        )
                    batch = []
        i += 1

    if batch:
        with _lock:
            _db.executemany(
                "INSERT INTO entries (server_id, tipo, nome, nome_lower, url, group_title, logo) VALUES (?,?,?,?,?,?,?)",
                batch,
            )

    with _lock:
        _db.commit()

    elapsed = time.time() - start
    s = {
        "filmes": filmes,
        "series": series,
        "total": filmes + series,
        "index_time": round(elapsed, 2),
    }
    _server_state[server_id] = {
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "path": path,
        "stats": s,
        "slug": slug,
    }
    print(f"[indexer] ✅ '{slug}' — {filmes} filmes, {series} séries em {elapsed:.1f}s")
    return s


def _build_all():
    """Indexa todos os servers ativos, recomputando stats globais."""
    _ensure_db()
    servers = _load_servers_cb() if _load_servers_cb else []
    # remove do índice qualquer server que não está mais ativo / removido
    active_ids = {s["id"] for s in servers if s.get("ativo", True)}
    with _lock:
        rows = _db.execute("SELECT DISTINCT server_id FROM entries").fetchall()
        for (sid,) in rows:
            if sid not in active_ids:
                _db.execute("DELETE FROM entries WHERE server_id=?", (sid,))
        _db.commit()
    # remove state de servers inativos
    for sid in list(_server_state.keys()):
        if sid not in active_ids:
            _server_state.pop(sid, None)

    for s in servers:
        if not s.get("ativo", True):
            continue
        _index_one(s)
    _refresh_global_stats()


def _refresh_global_stats():
    """Recalcula stats globais a partir do estado por server."""
    global _stats
    f = sum(st["stats"]["filmes"] for st in _server_state.values())
    s = sum(st["stats"]["series"] for st in _server_state.values())
    _stats = {"filmes": f, "series": s, "total": f + s, "index_time": 0}


def _ensure_index():
    """Garante que cada server ativo está indexado e atualizado."""
    if _db is None:
        _ensure_db()
    if not _load_servers_cb:
        return
    servers = _load_servers_cb()
    active_ids = {s["id"] for s in servers if s.get("ativo", True)}

    # remove inativos
    stale = [sid for sid in _server_state.keys() if sid not in active_ids]
    if stale:
        with _lock:
            for sid in stale:
                _db.execute("DELETE FROM entries WHERE server_id=?", (sid,))
            _db.commit()
        for sid in stale:
            _server_state.pop(sid, None)

    for s in servers:
        if not s.get("ativo", True):
            continue
        path = _resolve_path(s["filename"])
        if s["id"] not in _server_state or _file_changed(s["id"], path):
            _index_one(s)
    _refresh_global_stats()


# =========================================================
# ======================== AÇÕES ==========================
# =========================================================

def reindex(server_id=None):
    """Força re-indexação. Sem arg = todos (retorna stats globais).
    Com server_id = só aquele (retorna stats per-server com delta vs estado anterior)."""
    if not _load_servers_cb:
        return _stats
    if server_id is None:
        _build_all()
        return _stats
    servers = [s for s in _load_servers_cb() if s["id"] == server_id]
    if not servers:
        return _stats

    # Snapshot do estado anterior pra calcular delta de novo conteúdo
    prev_state = _server_state.get(server_id) or {}
    prev_stats = prev_state.get("stats") or {}
    prev_filmes = prev_stats.get("filmes", 0)
    prev_series = prev_stats.get("series", 0)
    had_previous = bool(prev_state)

    _index_one(servers[0])
    _refresh_global_stats()

    state = _server_state.get(server_id)
    if state and "stats" in state:
        s = dict(state["stats"])
        s["delta_filmes"] = s.get("filmes", 0) - prev_filmes
        s["delta_series"] = s.get("series", 0) - prev_series
        s["had_previous"] = had_previous
        return s
    return _stats


def drop_server(server_id):
    """Remove um server inteiro do índice (chamado quando o server é deletado)."""
    if _db is None:
        return
    with _lock:
        _db.execute("DELETE FROM entries WHERE server_id=?", (server_id,))
        _db.commit()
    _server_state.pop(server_id, None)
    _refresh_global_stats()


# =========================================================
# ======================== BUSCA ==========================
# =========================================================

def _search(query, tipo=None, server_id=None, limit=100):
    _ensure_index()
    if _db is None:
        return []
    q = f"%{query.lower()}%"
    sql = "SELECT nome, url, logo, group_title, tipo, server_id FROM entries WHERE nome_lower LIKE ?"
    params = [q]
    if tipo:
        sql += " AND tipo=?"
        params.append(tipo)
    if server_id:
        sql += " AND server_id=?"
        params.append(server_id)
    sql += " LIMIT ?"
    params.append(limit)
    with _lock:
        rows = _db.execute(sql, tuple(params)).fetchall()
    return rows


def search_filmes(query, limit=100, server_id=None):
    rows = _search(query, tipo="filme", server_id=server_id, limit=limit)
    return [{"nome": r[0], "link": r[1], "logo": r[2], "group": r[3], "server_id": r[5]} for r in rows]


def search_series(query, limit=50, server_id=None):
    rows = _search(query, tipo="serie", server_id=server_id, limit=limit)
    return [{"name": r[0], "url": r[1], "logo": r[2], "group": r[3], "server_id": r[5]} for r in rows]


def search_desenhos_filmes(query, limit=100, server_id=None):
    return search_filmes(query, limit=limit, server_id=server_id)


def search_desenhos_series(query, limit=50, server_id=None):
    return search_series(query, limit=limit, server_id=server_id)


def search_animes_filmes(query, limit=100, server_id=None):
    return search_filmes(query, limit=limit, server_id=server_id)


def search_animes_series(query, limit=50, server_id=None):
    return search_series(query, limit=limit, server_id=server_id)


def search_all(query, limit=100, server_id=None):
    rows = _search(query, tipo=None, server_id=server_id, limit=limit)
    out = []
    for r in rows:
        if r[4] == "canal":
            continue
        out.append({"nome": r[0], "link": r[1], "logo": r[2], "group": r[3], "tipo": r[4], "server_id": r[5]})
    return out


def get_stats():
    """Stats globais agregadas (legado)."""
    return _stats


def get_stats_per_server():
    """Stats por server."""
    out = {}
    for sid, st in _server_state.items():
        out[sid] = {**st["stats"], "slug": st.get("slug")}
    return out
