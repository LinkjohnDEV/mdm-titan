from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
import os
import shutil
import hashlib
import json
from functools import wraps
import requests
from datetime import datetime
import psycopg2.extras
from db import get_connection
import indexer
import re
import threading
import time

# Cache de uso de disco (atualizado em background a cada 10 min)
_disk_cache = {"used": 0, "total": 0, "ready": False}

def _refresh_disk_cache():
    while True:
        try:
            import subprocess as _sp
            r = _sp.run(['df', '-B1', '/mnt/media'], capture_output=True, text=True, timeout=10)
            parts = r.stdout.strip().split('\n')[-1].split()
            total = int(parts[1])
            du = _sp.run(['du', '-sb', '/mnt/media'], capture_output=True, text=True, timeout=600)
            used = int(du.stdout.split()[0]) if du.returncode == 0 else 0
            _disk_cache['total'] = total
            _disk_cache['used'] = used
            _disk_cache['ready'] = True
        except Exception:
            pass
        time.sleep(600)

threading.Thread(target=_refresh_disk_cache, daemon=True).start()

app = Flask(__name__)
app.secret_key = hashlib.sha256(
    os.getenv("DB_PASS", "mdm_fallback_secret").encode()
).hexdigest()
app.config['PERMANENT_SESSION_LIFETIME'] = 2592000 # 30 dias em segundos
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB


# =========================================================
# ===================== AUTH HELPERS =======================
# =========================================================

def hash_password(password):
    """Hash de senha com SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def login_required(f):
    """Decorator: requer login"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator: requer login + role admin"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        if session.get("role") != "admin":
            return redirect("/dashboard")
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_user_info():
    """Disponibiliza user e role em todos os templates"""
    return {
        "current_user": session.get("user"),
        "current_role": session.get("role", "user")
    }

# ==============================
# CONFIGURAÇÕES GERAIS
# ==============================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "dados.txt")  # legado — usado como fallback se não houver server cadastrado

# Pasta onde os arquivos M3U de cada server são guardados
SERVERS_DIR = os.path.join(BASE_DIR, "data", "servers")
os.makedirs(SERVERS_DIR, exist_ok=True)


def _load_m3u_servers_for_indexer():
    """Lê servers ativos do Postgres pra o indexer. Retorna lista de dicts."""
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, nome, slug, filename, ativo FROM m3u_servers WHERE ativo=true ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[indexer] erro carregando servers: {e}")
        return []

# Storage pools — descobertos automaticamente em /mnt/media*
import glob as _glob
STORAGE_GLOB = "/mnt/media*"
DEFAULT_STORAGE = "/mnt/media"

# Subpasta de cada categoria dentro do storage escolhido
CATEGORY_FOLDER = {
    "serie":          "series",
    "filme":          "filmes",
    "desenho":        "desenhos",
    "desenho_serie":  "desenhosseries",
    "anime":          "animes",
    "anime_serie":    "animeseries",
}

def list_storage_roots():
    """Lista paths de /mnt/media* que existem e são diretórios."""
    return sorted(p for p in _glob.glob(STORAGE_GLOB) if os.path.isdir(p))

def resolve_storage(raw):
    """Valida que o storage informado é um dos descobertos; senão usa o default."""
    if not raw:
        return DEFAULT_STORAGE
    raw = raw.rstrip("/")
    return raw if raw in list_storage_roots() else DEFAULT_STORAGE

def category_base(storage, tipo):
    """Monta {storage}/{subpasta-da-categoria}."""
    sub = CATEGORY_FOLDER.get(tipo, tipo)
    return os.path.join(storage, sub)

def get_category_base(data, tipo):
    """Atalho usado nas rotas: resolve storage do request e devolve a base da categoria."""
    raw = (data or {}).get("storage")
    return category_base(resolve_storage(raw), tipo)

def cross_pool_exists(nome_seguro, tipo, selected_storage):
    """Retorna lista de outros pools onde o conteúdo já existe (excluindo o selecionado)."""
    found = []
    for root in list_storage_roots():
        if root == selected_storage:
            continue
        path = os.path.join(category_base(root, tipo), nome_seguro)
        try:
            if os.path.isdir(path) and any(os.scandir(path)):
                found.append(root)
        except PermissionError:
            pass
    return found

# Constantes legadas — apontam pro storage padrão; mantidas pra backwards-compat
# nas funções que ainda não foram migradas pra receber storage por request.
SERIES_BASE = category_base(DEFAULT_STORAGE, "serie")
FILMES_BASE = category_base(DEFAULT_STORAGE, "filme")
DESENHOS_BASE = category_base(DEFAULT_STORAGE, "desenho")
DESENHOS_SERIES_BASE = category_base(DEFAULT_STORAGE, "desenho_serie")
ANIMES_BASE = category_base(DEFAULT_STORAGE, "anime")
ANIMES_SERIES_BASE = category_base(DEFAULT_STORAGE, "anime_serie")
MEDIA_BASE = DEFAULT_STORAGE

# Inicializa indexador de busca multi-server
indexer.init(BASE_DIR, _load_m3u_servers_for_indexer)


# =========================================================
# ======================== LOGIN ==========================
# =========================================================


@app.route("/sw.js")
def service_worker():
    return send_from_directory(os.path.join(app.root_path, "static"), "sw.js", mimetype="application/javascript")

@app.route("/manifest.json")
def pwa_manifest():
    return send_from_directory(os.path.join(app.root_path, "static"), "manifest.json", mimetype="application/manifest+json")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect("/dashboard")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        if not username or not password:
            return render_template("login.html", error="Preencha usuário e senha")

        try:
            conn = get_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                "SELECT * FROM users WHERE username=%s AND ativo=true",
                (username,)
            )
            user = cursor.fetchone()
            cursor.close()
            conn.close()

            if user:
                # Aceita senha em texto puro (legado) ou hash SHA-256
                senha_hash = hash_password(password)
                if user["password"] == senha_hash or user["password"] == password:
                    session["user"] = user["username"]
                    session["role"] = user["role"]
                    session["user_id"] = user["id"]
                    
                    if remember:
                        session.permanent = True
                    else:
                        session.permanent = False
                        
                    return redirect("/dashboard")

        except Exception as e:
            print(f"[login] Erro: {e}")

        return render_template("login.html", error="Usuário ou senha inválidos")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# =========================================================
# ======================== HOME ===========================
# =========================================================

@app.route("/")
def home():
    return redirect("/dashboard") if "user" in session else redirect("/login")


# =========================================================
# ======================= DASHBOARD =======================
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/dashboard_stats")
def dashboard_stats():
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Contagens gerais
    cursor.execute("""
        SELECT
            COUNT(*) FILTER (WHERE tipo='serie') as total_series,
            COUNT(*) FILTER (WHERE tipo='filme') as total_filmes,
            COUNT(*) FILTER (WHERE status='completed') as total_completed,
            COUNT(*) FILTER (WHERE status='failed') as total_failed,
            COUNT(*) FILTER (WHERE status='cancelled') as total_cancelled,
            COUNT(*) FILTER (WHERE status='downloading') as downloading_now,
            COUNT(*) FILTER (WHERE status='queued') as queued_now,
            COALESCE(SUM(episodios_baixados), 0) as total_episodios
        FROM jobs
    """)
    stats = cursor.fetchone()
    stats = {k: (v or 0) for k, v in stats.items()}

    # Webhooks ativos
    cursor.execute("SELECT COUNT(*) as total FROM webhooks WHERE ativo=true")
    webhooks_ativos = cursor.fetchone()["total"]

    # Downloads por dia - últimos 7 dias
    cursor.execute("""
        SELECT TO_CHAR(finished_at::date, 'DD/MM') as dia, COUNT(*) as total
        FROM jobs
        WHERE status='completed' AND finished_at >= NOW() - INTERVAL '7 days'
        GROUP BY finished_at::date, dia
        ORDER BY finished_at::date ASC
    """)
    downloads_7d_rows = cursor.fetchall()
    downloads_7d = [{"dia": r["dia"], "total": r["total"]} for r in downloads_7d_rows]

    # Últimos 50 jobs concluídos
    cursor.execute("""
        SELECT series_name, tipo, temporada, status, finished_at, episodios_baixados
        FROM jobs
        WHERE status IN ('completed','failed','cancelled')
        ORDER BY finished_at DESC NULLS LAST
        LIMIT 50
    """)
    recentes_rows = cursor.fetchall()
    recentes = []
    for r in recentes_rows:
        recentes.append({
            "nome": r["series_name"],
            "tipo": r["tipo"],
            "temporada": r["temporada"],
            "status": r["status"],
            "finished_at": r["finished_at"].strftime("%d/%m %H:%M") if r["finished_at"] else "--",
            "episodios": r["episodios_baixados"] or 0
        })

    # Jobs ativos agora (downloading)
    cursor.execute("""
        SELECT series_name, tipo, temporada, episodios_baixados, total_episodios
        FROM jobs WHERE status='downloading'
        ORDER BY id DESC
    """)
    ativos_rows = cursor.fetchall()
    ativos = [{"nome": r["series_name"], "tipo": r["tipo"], "temporada": r["temporada"],
                "ep_baixados": r["episodios_baixados"] or 0, "ep_total": r["total_episodios"] or 0}
              for r in ativos_rows]

    cursor.close()
    conn.close()

    # Disco — usa cache calculado por du em background (SMB mounts não reportam used via df)
    total = _disk_cache['total']
    used  = _disk_cache['used']
    if total == 0:
        try:
            total, _, _ = shutil.disk_usage(MEDIA_BASE)
        except Exception:
            total = 0
    disk_percent = round((used / total) * 100, 1) if total > 0 else 0

    # Uptime
    with open("/proc/uptime") as f:
        uptime_seconds = float(f.readline().split()[0])
    uptime_days = int(uptime_seconds // 86400)
    uptime_hours = int((uptime_seconds % 86400) // 3600)
    uptime_mins = int((uptime_seconds % 3600) // 60)

    # Index stats
    idx = indexer.get_stats()

    return jsonify({
        "series": stats["total_series"],
        "filmes": stats["total_filmes"],
        "completed": stats["total_completed"],
        "failed": stats["total_failed"],
        "cancelled": stats["total_cancelled"],
        "downloading_now": stats["downloading_now"],
        "queued_now": stats["queued_now"],
        "total_episodios": int(stats["total_episodios"]),
        "disk_total_gb": round(total / (1024**3), 1),
        "disk_used_gb": round(used / (1024**3), 1),
        "disk_percent": disk_percent,
        "webhooks": webhooks_ativos,
        "uptime": f"{uptime_days}d {uptime_hours}h {uptime_mins}m",
        "downloads_7d": downloads_7d,
        "recentes": recentes,
        "ativos": ativos,
        "index_filmes": idx.get("filmes", 0),
        "index_series": idx.get("series", 0),
        "index_total": idx.get("total", 0),
        "index_time": idx.get("index_time", 0)
    })


# =========================================================
# ======================= SÉRIES ==========================
# =========================================================

@app.route("/series")
@login_required
def series_page():
    return render_template("series.html")


def _q_server_id():
    """Lê server_id da query string. Aceita string vazia / inválida = None (todos)."""
    raw = request.args.get("server_id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


@app.route("/buscar_series")
def buscar_series():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return {"results": []}
    resultados = indexer.search_series(termo, server_id=_q_server_id())
    return {"results": resultados}


@app.route("/add_serie", methods=["POST"])
def add_serie():
    data = request.json
    nome = data.get("name")
    temporada = data.get("season")
    episodios = data.get("episodes")

    if not nome or not temporada or not episodios:
        return {"message": "Dados inválidos"}, 400
        
    force = data.get("force", False)
    selected_storage = resolve_storage(data.get("storage"))
    serie_path = os.path.join(category_base(selected_storage, "serie"), nome)
    season_path = os.path.join(serie_path, f"Season {temporada}")

    if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
        return {"error": "exists", "message": "Esta temporada já existe no servidor. Deseja sobrescrevê-la e baixar novamente?"}, 409

    if not force:
        outros = cross_pool_exists(os.path.join(nome, f"Season {temporada}"), "serie", selected_storage)
        if outros:
            return {"error": "exists", "message": f"Season {temporada} de {nome} já existe em {', '.join(outros)}. Deseja baixar mesmo assim em {selected_storage}?"}, 409

    os.makedirs(season_path, exist_ok=True)

    links_file = os.path.join(season_path, "links.txt")
    modo = "a" if os.path.exists(links_file) else "w"

    with open(links_file, modo, encoding="utf-8") as f:
        for ep in episodios:
            f.write(f"{ep['name'].strip()}|{ep['url'].strip()}\n")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO jobs
        (series_name, tipo, temporada, caminho,
         status, sobrescrever, total_episodios, episodios_baixados)
        VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
    """, (nome, temporada, season_path, bool(force), len(episodios)))

    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "Temporada adicionada à fila!"}



@app.route("/add_serie_completa", methods=["POST"])
def add_serie_completa():
    data = request.json
    nome = data.get("name")
    seasons = data.get("seasons")  # list of {season, episodes}

    if not nome or not seasons or not isinstance(seasons, list):
        return {"message": "Dados inválidos"}, 400

    force = data.get("force", False)
    selected_storage = resolve_storage(data.get("storage"))
    queued = 0
    exists_seasons = []
    cross_seasons = []

    conn = get_connection()
    cursor = conn.cursor()

    for s in seasons:
        temporada = s.get("season")
        episodios = s.get("episodes")
        if not temporada or not episodios:
            continue

        serie_path = os.path.join(category_base(selected_storage, "serie"), nome)
        season_path = os.path.join(serie_path, f"Season {temporada}")

        if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
            exists_seasons.append(temporada)
            continue

        if not force:
            outros = cross_pool_exists(os.path.join(nome, f"Season {temporada}"), "serie", selected_storage)
            if outros:
                cross_seasons.append((temporada, outros))
                continue

        os.makedirs(season_path, exist_ok=True)

        links_file = os.path.join(season_path, "links.txt")
        modo = "a" if os.path.exists(links_file) else "w"
        with open(links_file, modo, encoding="utf-8") as f:
            for ep in episodios:
                f.write(f"{ep['name'].strip()}|{ep['url'].strip()}\n")

        cursor.execute("""
            INSERT INTO jobs
            (series_name, tipo, temporada, caminho,
             status, sobrescrever, total_episodios, episodios_baixados)
            VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
        """, (nome, temporada, season_path, bool(force), len(episodios)))
        queued += 1

    conn.commit()
    cursor.close()
    conn.close()

    if queued == 0 and cross_seasons:
        partes = "; ".join(f"T{t} em {', '.join(p)}" for t, p in cross_seasons)
        return {"error": "exists", "message": f"Temporada(s) já existem em outro storage: {partes}. Deseja baixar mesmo assim em {selected_storage}?"}, 409

    if queued == 0 and exists_seasons:
        return {
            "error": "exists",
            "message": f"As temporadas {', '.join(exists_seasons)} já existem no servidor. Deseja sobrescrevê-las e baixar novamente?"
        }, 409

    return {"message": f"{queued} temporada(s) adicionada(s) à fila!", "queued": queued}


# =========================================================
# ======================= BUSCAR FILMES ===================
# =========================================================

@app.route("/search_filme")
def search_filme():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])
    resultados = indexer.search_filmes(query, server_id=_q_server_id())
    return jsonify(resultados)


@app.route("/api/reindex", methods=["POST"])
def api_reindex():
    """Força re-indexação do dados.txt"""
    stats = indexer.reindex()
    return jsonify({"status": "ok", "stats": stats})


@app.route("/api/index_stats")
def api_index_stats():
    """Retorna estatísticas do índice"""
    return jsonify(indexer.get_stats())



# =========================================================
# ======================= FILMES ==========================
# =========================================================

@app.route("/filmes")
@login_required
def filmes():
    return render_template("filmes.html")


@app.route("/add_filme", methods=["POST"])
def add_filme():
    data = request.json
    nome = data.get("name")
    link = data.get("link")

    if not nome or not link:
        return {"message": "Dados inválidos"}, 400

    # Evita erros em discos montados via SMB/NTFS que não aceitam : e outros caracteres especiais
    nome_seguro = re.sub(r'[\\/*?:"<>|]', "", nome).strip()

    force = data.get("force", False)
    selected_storage = resolve_storage(data.get("storage"))
    pasta = os.path.join(category_base(selected_storage, "filme"), nome_seguro)

    if os.path.exists(pasta) and any(os.scandir(pasta)) and not force:
        return {"error": "exists", "message": "Este filme já existe no servidor. Deseja sobrescrevê-lo e baixar novamente?"}, 409

    if not force:
        outros = cross_pool_exists(nome_seguro, "filme", selected_storage)
        if outros:
            return {"error": "exists", "message": f"Este filme já existe em {', '.join(outros)}. Deseja baixar mesmo assim em {selected_storage}?"}, 409

    os.makedirs(pasta, exist_ok=True)

    # Se estiver sobrescrevendo ou já existir, usar "w" para reescrever o arquivo links.txt praquele job
    with open(os.path.join(pasta, "links.txt"), "w") as f:
        f.write(link)

    # Se for force=True e já havia job antigo no DB, marcamos o status
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO jobs
            (series_name, tipo, temporada, caminho, status, sobrescrever)
            VALUES (%s, 'filme', NULL, %s, 'queued', %s)
        """, (nome_seguro, pasta, bool(force)))

        conn.commit()
    except Exception as e:
        error_msg = str(e)
        if "unique" in error_msg.lower():
            # A chave única já existe, vamos dar UPDATE para queued de volta
            conn.rollback()
            cursor.execute("""
                UPDATE jobs
                SET status='queued', sobrescrever=%s, finished_at=NULL
                WHERE series_name=%s AND tipo='filme'
            """, (bool(force), nome_seguro))
            conn.commit()
        else:
            if 'conn' in locals() and conn: conn.rollback()
            if 'cursor' in locals() and cursor: cursor.close()
            if 'conn' in locals() and conn: conn.close()
            return {"error": f"Erro de BD: {error_msg}"}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()

    return {"message": "Filme adicionado à fila!"}


# =========================================================
# ======================= DESENHOS ========================
# =========================================================

@app.route("/desenhos")
@login_required
def desenhos_page():
    return render_template("paused.html", page_name="Desenhos")


@app.route("/search_desenho_filme")
def search_desenho_filme():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])
    return jsonify(indexer.search_desenhos_filmes(query, server_id=_q_server_id()))


@app.route("/search_desenho_serie")
def search_desenho_serie():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return {"results": []}
    return {"results": indexer.search_desenhos_series(termo, server_id=_q_server_id())}


@app.route("/add_desenho_filme", methods=["POST"])
def add_desenho_filme():
    data = request.json
    nome = data.get("name")
    link = data.get("link")

    if not nome or not link:
        return {"message": "Dados inválidos"}, 400

    nome_seguro = re.sub(r'[\\/*?:"<>|]', "", nome).strip()
    force = data.get("force", False)
    pasta = os.path.join(get_category_base(data, "desenho"), nome_seguro)

    if os.path.exists(pasta) and any(os.scandir(pasta)) and not force:
        return {"error": "exists", "message": "Este desenho já existe no servidor. Deseja sobrescrevê-lo e baixar novamente?"}, 409

    os.makedirs(pasta, exist_ok=True)

    with open(os.path.join(pasta, "links.txt"), "w") as f:
        f.write(link)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO jobs
            (series_name, tipo, temporada, caminho, status, sobrescrever)
            VALUES (%s, 'filme', NULL, %s, 'queued', %s)
        """, (nome_seguro, pasta, bool(force)))
        conn.commit()
    except Exception as e:
        error_msg = str(e)
        if "unique" in error_msg.lower():
            conn.rollback()
            cursor.execute("""
                UPDATE jobs SET status='queued', sobrescrever=%s, finished_at=NULL
                WHERE series_name=%s AND tipo='filme' AND caminho=%s
            """, (bool(force), nome_seguro, pasta))
            conn.commit()
        else:
            if 'conn' in locals() and conn: conn.rollback()
            if 'cursor' in locals() and cursor: cursor.close()
            if 'conn' in locals() and conn: conn.close()
            return {"error": f"Erro de BD: {error_msg}"}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()

    return {"message": "Desenho adicionado à fila!"}


@app.route("/add_desenho_serie", methods=["POST"])
def add_desenho_serie():
    data = request.json
    nome = data.get("name")
    temporada = data.get("season")
    episodios = data.get("episodes")

    if not nome or not temporada or not episodios:
        return {"message": "Dados inválidos"}, 400

    force = data.get("force", False)
    serie_path = os.path.join(get_category_base(data, "desenho_serie"), nome)
    season_path = os.path.join(serie_path, f"Season {temporada}")

    if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
        return {"error": "exists", "message": "Esta temporada já existe no servidor. Deseja sobrescrevê-la e baixar novamente?"}, 409

    os.makedirs(season_path, exist_ok=True)

    links_file = os.path.join(season_path, "links.txt")
    modo = "a" if os.path.exists(links_file) else "w"
    with open(links_file, modo, encoding="utf-8") as f:
        for ep in episodios:
            f.write(f"{ep['name'].strip()}|{ep['url'].strip()}\n")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs
        (series_name, tipo, temporada, caminho, status, sobrescrever, total_episodios, episodios_baixados)
        VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
    """, (nome, temporada, season_path, bool(force), len(episodios)))
    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "Temporada de desenho adicionada à fila!"}


@app.route("/add_desenho_serie_completa", methods=["POST"])
def add_desenho_serie_completa():
    data = request.json
    nome = data.get("name")
    seasons = data.get("seasons")
    if not nome or not seasons or not isinstance(seasons, list):
        return {"message": "Dados inválidos"}, 400
    force = data.get("force", False)
    queued = 0
    exists_seasons = []
    conn = get_connection()
    cursor = conn.cursor()
    for s in seasons:
        temporada = s.get("season")
        episodios = s.get("episodes")
        if not temporada or not episodios:
            continue
        serie_path = os.path.join(get_category_base(data, "desenho_serie"), nome)
        season_path = os.path.join(serie_path, f"Season {temporada}")
        if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
            exists_seasons.append(temporada)
            continue
        os.makedirs(season_path, exist_ok=True)
        links_file = os.path.join(season_path, "links.txt")
        modo = "a" if os.path.exists(links_file) else "w"
        with open(links_file, modo, encoding="utf-8") as f:
            for ep in episodios:
                f.write(f"{ep['name'].strip()}|{ep['url'].strip()}\n")
        cursor.execute("""
            INSERT INTO jobs
            (series_name, tipo, temporada, caminho, status, sobrescrever, total_episodios, episodios_baixados)
            VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
        """, (nome, temporada, season_path, bool(force), len(episodios)))
        queued += 1
    conn.commit()
    cursor.close()
    conn.close()
    if queued == 0 and exists_seasons:
        return {"error": "exists", "message": f"As temporadas {', '.join(exists_seasons)} já existem. Deseja sobrescrevê-las?"}, 409
    return {"message": f"{queued} temporada(s) de desenho adicionada(s) à fila!", "queued": queued}


# =========================================================
# ======================== ANIMES =========================
# =========================================================

@app.route("/animes")
@login_required
def animes_page():
    return render_template("paused.html", page_name="Animes")


@app.route("/search_anime_filme")
def search_anime_filme():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])
    return jsonify(indexer.search_animes_filmes(query, server_id=_q_server_id()))


@app.route("/search_anime_serie")
def search_anime_serie():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return {"results": []}
    return {"results": indexer.search_animes_series(termo, server_id=_q_server_id())}


@app.route("/add_anime_filme", methods=["POST"])
def add_anime_filme():
    data = request.json
    nome = data.get("name")
    link = data.get("link")

    if not nome or not link:
        return {"message": "Dados inválidos"}, 400

    nome_seguro = re.sub(r'[\\/*?:"<>|]', "", nome).strip()
    force = data.get("force", False)
    pasta = os.path.join(get_category_base(data, "anime"), nome_seguro)

    if os.path.exists(pasta) and any(os.scandir(pasta)) and not force:
        return {"error": "exists", "message": "Este anime já existe no servidor. Deseja sobrescrevê-lo e baixar novamente?"}, 409

    os.makedirs(pasta, exist_ok=True)

    with open(os.path.join(pasta, "links.txt"), "w") as f:
        f.write(link)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO jobs
            (series_name, tipo, temporada, caminho, status, sobrescrever)
            VALUES (%s, 'filme', NULL, %s, 'queued', %s)
        """, (nome_seguro, pasta, bool(force)))
        conn.commit()
    except Exception as e:
        error_msg = str(e)
        if "unique" in error_msg.lower():
            conn.rollback()
            cursor.execute("""
                UPDATE jobs SET status='queued', sobrescrever=%s, finished_at=NULL
                WHERE series_name=%s AND tipo='filme' AND caminho=%s
            """, (bool(force), nome_seguro, pasta))
            conn.commit()
        else:
            if 'conn' in locals() and conn: conn.rollback()
            if 'cursor' in locals() and cursor: cursor.close()
            if 'conn' in locals() and conn: conn.close()
            return {"error": f"Erro de BD: {error_msg}"}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()

    return {"message": "Anime adicionado à fila!"}


@app.route("/add_anime_serie", methods=["POST"])
def add_anime_serie():
    data = request.json
    nome = data.get("name")
    temporada = data.get("season")
    episodios = data.get("episodes")

    if not nome or not temporada or not episodios:
        return {"message": "Dados inválidos"}, 400

    force = data.get("force", False)
    serie_path = os.path.join(get_category_base(data, "anime_serie"), nome)
    season_path = os.path.join(serie_path, f"Season {temporada}")

    if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
        return {"error": "exists", "message": "Esta temporada já existe no servidor. Deseja sobrescrevê-la e baixar novamente?"}, 409

    os.makedirs(season_path, exist_ok=True)

    links_file = os.path.join(season_path, "links.txt")
    modo = "a" if os.path.exists(links_file) else "w"
    with open(links_file, modo, encoding="utf-8") as f:
        for ep in episodios:
            f.write(f"{ep['name'].strip()}|{ep['url'].strip()}\n")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs
        (series_name, tipo, temporada, caminho, status, sobrescrever, total_episodios, episodios_baixados)
        VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
    """, (nome, temporada, season_path, bool(force), len(episodios)))
    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "Temporada de anime adicionada à fila!"}


@app.route("/add_anime_serie_completa", methods=["POST"])
def add_anime_serie_completa():
    data = request.json
    nome = data.get("name")
    seasons = data.get("seasons")
    if not nome or not seasons or not isinstance(seasons, list):
        return {"message": "Dados inválidos"}, 400
    force = data.get("force", False)
    queued = 0
    exists_seasons = []
    conn = get_connection()
    cursor = conn.cursor()
    for s in seasons:
        temporada = s.get("season")
        episodios = s.get("episodes")
        if not temporada or not episodios:
            continue
        serie_path = os.path.join(get_category_base(data, "anime_serie"), nome)
        season_path = os.path.join(serie_path, f"Season {temporada}")
        if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
            exists_seasons.append(temporada)
            continue
        os.makedirs(season_path, exist_ok=True)
        links_file = os.path.join(season_path, "links.txt")
        modo = "a" if os.path.exists(links_file) else "w"
        with open(links_file, modo, encoding="utf-8") as f:
            for ep in episodios:
                f.write(f"{ep['name'].strip()}|{ep['url'].strip()}\n")
        cursor.execute("""
            INSERT INTO jobs
            (series_name, tipo, temporada, caminho, status, sobrescrever, total_episodios, episodios_baixados)
            VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
        """, (nome, temporada, season_path, bool(force), len(episodios)))
        queued += 1
    conn.commit()
    cursor.close()
    conn.close()
    if queued == 0 and exists_seasons:
        return {"error": "exists", "message": f"As temporadas {', '.join(exists_seasons)} já existem. Deseja sobrescrevê-las?"}, 409
    return {"message": f"{queued} temporada(s) de anime adicionada(s) à fila!", "queued": queued}


# =========================================================
# ================= STORAGE FILE MANAGEMENT ===============
# =========================================================

def _safe_path(relative, base=None):
    """Resolve um caminho relativo dentro do storage base, com segurança.
    Se base não for informado, usa MEDIA_BASE (legado)."""
    b = base or MEDIA_BASE
    full = os.path.realpath(os.path.join(b, (relative or "").lstrip("/")))
    if not full.startswith(os.path.realpath(b)):
        return None
    return full


# Pastas raiz por categoria — nunca podem ser deletadas pelo Explorer
PROTECTED_CATEGORY_FOLDERS = set(CATEGORY_FOLDER.values()) | {"filmes", "series"}


@app.route("/api/storage_pools")
@login_required
def storage_pools():
    """Lista os storage pools disponíveis em /mnt/media* com uso e status de cor.

    Cores:
      - verde  (green)  : <= 59%
      - laranja (orange): 60–85%
      - vermelho (red)  : >= 86%
    """
    pools = []
    for path in list_storage_roots():
        try:
            total, used, free = shutil.disk_usage(path)
            pct = (used / total * 100) if total else 0
            if pct >= 86:
                status = "red"
            elif pct >= 60:
                status = "orange"
            else:
                status = "green"
            pools.append({
                "path": path,
                "label": os.path.basename(path) or path,
                "used_pct": round(pct, 1),
                "used_human": _human_bytes(used),
                "free_human": _human_bytes(free),
                "total_human": _human_bytes(total),
                "status": status,
                "is_default": path == DEFAULT_STORAGE,
            })
        except Exception as e:
            pools.append({
                "path": path,
                "label": os.path.basename(path) or path,
                "error": str(e),
                "status": "red",
            })
    return jsonify(pools)


def _human_bytes(n):
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} EB"


@app.route("/api/storage/mkdir", methods=["POST"])
@login_required
def storage_mkdir():
    data = request.json or {}
    pool = resolve_storage(data.get("pool"))
    path = _safe_path(data.get("path", ""), base=pool)
    if not path:
        return {"error": "Caminho inválido"}, 400
    if os.path.exists(path):
        return {"error": "Pasta já existe"}, 409
    os.makedirs(path)
    return {"status": "ok"}


@app.route("/api/storage/rename", methods=["POST"])
@login_required
def storage_rename():
    data = request.json or {}
    pool = resolve_storage(data.get("pool"))
    src = _safe_path(data.get("path", ""), base=pool)
    new_name = (data.get("new_name") or "").strip()
    if not src or not new_name:
        return {"error": "Parâmetros inválidos"}, 400
    if "/" in new_name or "\\" in new_name:
        return {"error": "Nome inválido"}, 400
    if not os.path.exists(src):
        return {"error": "Origem não encontrada"}, 404
    dst = os.path.join(os.path.dirname(src), new_name)
    dst_safe = _safe_path(os.path.relpath(dst, pool), base=pool)
    if not dst_safe:
        return {"error": "Destino inválido"}, 400
    if os.path.exists(dst_safe):
        return {"error": "Já existe um item com esse nome"}, 409
    os.rename(src, dst_safe)
    return {"status": "ok"}


@app.route("/api/storage/folders")
@login_required
def storage_folders():
    """Lista pastas de primeiro nível dentro do storage escolhido."""
    pool = resolve_storage(request.args.get("pool"))
    try:
        folders = sorted([
            d for d in os.listdir(pool)
            if os.path.isdir(os.path.join(pool, d))
        ])
        return {"folders": folders, "pool": pool}
    except Exception as e:
        return {"folders": [], "error": str(e)}


@app.route("/api/storage/move", methods=["POST"])
@login_required
def storage_move():
    data = request.json or {}
    pool = resolve_storage(data.get("pool"))
    src = _safe_path(data.get("src", ""), base=pool)
    dst_dir = _safe_path(data.get("dst_dir", ""), base=pool)
    if not src or not dst_dir:
        return {"error": "Caminho inválido"}, 400
    if not os.path.exists(src):
        return {"error": "Origem não encontrada"}, 404
    item_name = os.path.basename(src)
    final_dst = os.path.join(dst_dir, item_name)
    if os.path.exists(final_dst):
        return {"error": f"Já existe '{item_name}' na pasta de destino"}, 409
    os.makedirs(dst_dir, exist_ok=True)
    shutil.move(src, final_dst)
    return {"status": "ok"}


@app.route("/api/storage/delete", methods=["POST"])
@login_required
def storage_delete():
    """Deleta uma PASTA (não arquivo) dentro do storage.

    Travas de segurança:
      - Só apaga diretórios; arquivos isolados são bloqueados.
      - Nunca apaga o próprio storage root.
      - Nunca apaga as pastas-categoria de primeiro nível (filmes, series, etc).
    """
    data = request.json or {}
    pool = resolve_storage(data.get("pool"))
    rel = (data.get("path") or "").strip().lstrip("/")
    if not rel:
        return {"error": "Caminho obrigatório"}, 400

    target = _safe_path(rel, base=pool)
    if not target:
        return {"error": "Caminho inválido"}, 400
    if not os.path.exists(target):
        return {"error": "Pasta não encontrada"}, 404
    if os.path.isfile(target):
        return {"error": "Não é permitido apagar arquivos individuais — só pastas"}, 400

    # Bloqueia raiz e pastas de categoria
    real_target = os.path.realpath(target)
    real_pool = os.path.realpath(pool)
    if real_target == real_pool:
        return {"error": "Não é permitido apagar a raiz do storage"}, 400
    # Se a pasta é filha direta do pool e o nome é uma categoria protegida → bloqueia
    parent = os.path.dirname(real_target)
    leaf = os.path.basename(real_target)
    if parent == real_pool and leaf in PROTECTED_CATEGORY_FOLDERS:
        return {"error": f"Não é permitido apagar a pasta principal '{leaf}'"}, 400

    try:
        shutil.rmtree(target)
        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}, 500


# =========================================================
# ======================= STORAGE =========================
# =========================================================

from flask import send_file

@app.route("/storage")
@app.route("/storage/<path:subpath>")
@login_required
def storage(subpath=""):
    pool = resolve_storage(request.args.get("pool"))
    real_pool = os.path.realpath(pool)

    current_path = os.path.join(pool, subpath)
    if not os.path.realpath(current_path).startswith(real_pool):
        return "Acesso inválido"

    if os.path.exists(current_path) and os.path.isfile(current_path):
        return send_file(current_path)

    items = []
    if os.path.exists(current_path):
        for item in os.listdir(current_path):
            item = item.encode('utf-8', errors='replace').decode('utf-8')
            full = os.path.join(current_path, item)
            is_dir = os.path.isdir(full)
            size_str = ""
            if not is_dir:
                try:
                    b = os.path.getsize(full)
                    if b >= 1024**3:
                        size_str = f"{b/1024**3:.1f} GB"
                    elif b >= 1024**2:
                        size_str = f"{b/1024**2:.0f} MB"
                    else:
                        size_str = f"{b/1024:.0f} KB"
                except Exception:
                    size_str = ""

            # Flag: este item pode ser deletado?
            # - Não pode se for arquivo
            # - Não pode se for pasta-categoria na raiz do storage
            can_delete = False
            if is_dir:
                rel_full = os.path.relpath(full, real_pool)
                parts = rel_full.split(os.sep)
                if not (len(parts) == 1 and parts[0] in PROTECTED_CATEGORY_FOLDERS):
                    can_delete = True

            items.append({
                "name": item,
                "is_dir": is_dir,
                "size": size_str,
                "can_delete": can_delete,
            })

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    if subpath:
        parent_dir = os.path.dirname(subpath)
        parent = f"/{parent_dir}" if parent_dir else ""
    else:
        parent = None

    return render_template(
        "storage.html",
        items=items,
        current=subpath,
        parent=parent,
        pool=pool,
    )


# =========================================================
# ========================= MÍDIA =========================
# =========================================================

@app.route("/tendencias")
@login_required
def tendencias_page():
    return render_template("tendencias.html")

@app.route("/api/tendencias/<time_window>")
@login_required
def api_tendencias(time_window):
    # time_window deve ser 'day' ou 'week'
    if time_window not in ['day', 'week']:
        return jsonify({"error": "Janela de tempo inválida"}), 400
        
    api_key = os.getenv("TMDB_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Chave TMDB_API_KEY não configurada no servidor"}), 500
        
    url = f"https://api.themoviedb.org/3/trending/all/{time_window}?api_key={api_key}&language=pt-BR"
    
    try:
        import requests
        r = requests.get(url, timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/populares/<category>")
@login_required
def api_populares(category):
    # category suporta: streaming, tv, rent, theaters
    api_key = os.getenv("TMDB_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Chave TMDB_API_KEY não configurada"}), 500
        
    base_url = "https://api.themoviedb.org/3"
    
    if category == 'streaming':
        # Filmes populares num geral batem com o "Streaming" do TMDB site
        url = f"{base_url}/movie/popular?api_key={api_key}&language=pt-BR&page=1"
    elif category == 'tv':
        url = f"{base_url}/tv/popular?api_key={api_key}&language=pt-BR&page=1"
    elif category == 'rent':
        # TMDB discover para monetização = rent
        url = f"{base_url}/discover/movie?api_key={api_key}&language=pt-BR&with_watch_monetization_types=rent&sort_by=popularity.desc"
    elif category == 'theaters':
        # TMDB now_playing pro cinemas
        url = f"{base_url}/movie/now_playing?api_key={api_key}&language=pt-BR&page=1"
    else:
        return jsonify({"error": "Categoria Popular não reconhecida"}), 400
        
    try:
        import requests
        r = requests.get(url, timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================================================
# ====================== CORINGA ==========================
# =========================================================

@app.route("/coringa")
@login_required
def coringa():
    return render_template("coringa.html")


@app.route("/search_coringa", methods=["POST"])
@login_required
def search_coringa():
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify([])
    sid = data.get("server_id")
    try: sid = int(sid) if sid else None
    except (TypeError, ValueError): sid = None
    resultados = indexer.search_all(query, limit=100, server_id=sid)
    return jsonify(resultados)


@app.route("/api/storage/browse")
@login_required
def storage_browse():
    """Lista subpastas de um caminho relativo, dentro do storage escolhido."""
    rel = request.args.get("path", "")
    storage_root = resolve_storage(request.args.get("storage"))
    current = os.path.realpath(os.path.join(storage_root, rel.lstrip("/")))
    if not current.startswith(os.path.realpath(storage_root)):
        return jsonify({"error": "Caminho inválido"}), 400
    try:
        folders = sorted([
            d for d in os.listdir(current)
            if os.path.isdir(os.path.join(current, d))
        ])
        rel_current = os.path.relpath(current, storage_root)
        if rel_current == ".":
            rel_current = ""
        parent = os.path.relpath(os.path.dirname(current), storage_root) if rel_current else None
        if parent == ".":
            parent = ""
        return jsonify({"path": rel_current, "parent": parent, "folders": folders, "storage": storage_root})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/add_coringa", methods=["POST"])
@login_required
def add_coringa():
    data = request.json
    dest_rel = (data.get("dest") or "").strip()

    # Aceita lista de items ou item único (retrocompatibilidade)
    items = data.get("items")
    if not items:
        nome = (data.get("name") or "").strip()
        link = (data.get("link") or "").strip()
        if not nome or not link:
            return jsonify({"message": "Dados inválidos"}), 400
        items = [{"name": nome, "link": link}]

    storage_root = resolve_storage(data.get("storage"))
    dest_abs = os.path.realpath(os.path.join(storage_root, dest_rel.lstrip("/")))
    if not dest_abs.startswith(os.path.realpath(storage_root)):
        return jsonify({"message": "Destino inválido"}), 400

    jobs_criados = 0
    try:
        conn = get_connection()
        cursor = conn.cursor()
        for item in items:
            nome = (item.get("name") or "").strip()
            link = (item.get("link") or "").strip()
            if not nome or not link:
                continue
            nome_seguro = re.sub(r'[\\/*?:"<>|]', "", nome).strip()
            pasta = os.path.join(dest_abs, nome_seguro)
            os.makedirs(pasta, exist_ok=True)
            links_file = os.path.join(pasta, "links.txt")
            with open(links_file, "w") as f:
                f.write(link)
            cursor.execute("""
                INSERT INTO jobs (series_name, tipo, temporada, caminho, status, sobrescrever)
                VALUES (%s, 'filme', NULL, %s, 'queued', false)
            """, (nome_seguro, pasta))
            jobs_criados += 1
        conn.commit()
    except Exception as e:
        if 'conn' in locals() and conn: conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

    label = f"'{items[0].get('name', '')}'" if jobs_criados == 1 else f"{jobs_criados} arquivos"
    return jsonify({"message": f"Download coringa iniciado para {label}!"})


@app.route("/run_script", methods=["POST"])
@login_required
def run_script():
    data = request.json
    script = data.get("script")
    force = data.get("force", False)
    dest_rel = (data.get("dest") or "").strip()

    if not script or not isinstance(script, dict):
        return jsonify({"message": "Script inválido"}), 400

    tipo = (script.get("tipo") or "").lower()

    storage_root = resolve_storage(data.get("storage"))

    TIPOS_VALIDOS = ("serie", "filme", "filmes_batch", "desenho", "anime", "desenho_serie", "anime_serie")
    if tipo not in TIPOS_VALIDOS:
        return jsonify({"message": f"Tipo '{tipo}' não reconhecido. Use: {', '.join(TIPOS_VALIDOS)}"}), 400

    if dest_rel:
        # dest relativo é resolvido dentro do storage escolhido
        dest_abs = os.path.realpath(os.path.join(storage_root, dest_rel.lstrip("/")))
        if not dest_abs.startswith(os.path.realpath(storage_root)):
            return jsonify({"message": "Destino inválido"}), 400
        base_path = dest_abs
    else:
        # filmes_batch usa a base de filmes
        tipo_para_base = "filme" if tipo == "filmes_batch" else tipo
        base_path = category_base(storage_root, tipo_para_base)

    # filmes_batch não usa campo "nome" no topo
    if tipo != "filmes_batch":
        nome = (script.get("nome") or "").strip()
        if not nome:
            return jsonify({"message": "Campo 'nome' obrigatório no script"}), 400

    conn = get_connection()
    cursor = conn.cursor()
    queued = 0
    exists_list = []

    try:
        if tipo in ("serie", "desenho_serie", "anime_serie"):
            # Multi-season script
            temporadas = script.get("temporadas") or []
            if not temporadas:
                return jsonify({"message": "Nenhuma temporada encontrada no script"}), 400

            for t in temporadas:
                temporada = str(t.get("temporada", "")).strip()
                episodios = t.get("episodios") or []
                if not temporada or not episodios:
                    continue

                serie_path = os.path.join(base_path, nome)
                season_path = os.path.join(serie_path, f"Season {temporada}")

                if os.path.exists(season_path) and any(os.scandir(season_path)) and not force:
                    exists_list.append(temporada)
                    continue

                if not force:
                    outros = cross_pool_exists(os.path.join(nome, f"Season {temporada}"), "serie", storage_root)
                    if outros:
                        exists_list.append(f"{temporada}(em {', '.join(outros)})")
                        continue

                os.makedirs(season_path, exist_ok=True)
                links_file = os.path.join(season_path, "links.txt")
                with open(links_file, "w", encoding="utf-8") as f:
                    for ep in episodios:
                        ep_nome = (ep.get("nome") or ep.get("name") or "").strip()
                        ep_url = (ep.get("url") or "").strip()
                        if ep_nome and ep_url:
                            f.write(f"{ep_nome}|{ep_url}\n")

                cursor.execute("""
                    INSERT INTO jobs
                    (series_name, tipo, temporada, caminho, status, sobrescrever, total_episodios, episodios_baixados)
                    VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
                """, (nome, temporada, season_path, bool(force), len(episodios)))
                queued += 1

            if queued == 0 and exists_list:
                return jsonify({"message": f"As temporadas {', '.join(exists_list)} já existem. Deseja sobrescrever?"}), 409

        elif tipo == "filmes_batch":
            # Batch de filmes: cada item vai para sua própria pasta
            filmes = script.get("filmes") or []
            if not filmes:
                return jsonify({"message": "Nenhum filme encontrado no script"}), 400

            for filme in filmes:
                f_nome = re.sub(r'[\\/*?:"<>|]', "", (filme.get("nome") or "").strip())
                f_url  = (filme.get("url") or "").strip()
                if not f_nome or not f_url:
                    continue

                dest_path = os.path.join(base_path, f_nome)
                if os.path.exists(dest_path) and any(os.scandir(dest_path)) and not force:
                    exists_list.append(f_nome)
                    continue

                if not force:
                    outros = cross_pool_exists(f_nome, "filme", storage_root)
                    if outros:
                        exists_list.append(f"{f_nome}(em {', '.join(outros)})")
                        continue

                os.makedirs(dest_path, exist_ok=True)
                links_file = os.path.join(dest_path, "links.txt")
                with open(links_file, "w", encoding="utf-8") as lf:
                    lf.write(f_url)

                cursor.execute("""
                    INSERT INTO jobs (series_name, tipo, temporada, caminho, status, sobrescrever)
                    VALUES (%s, 'filme', NULL, %s, 'queued', %s)
                """, (f_nome, dest_path, bool(force)))
                queued += 1

            if queued == 0 and exists_list:
                return jsonify({"message": f"Os filmes já existem: {', '.join(exists_list)}. Deseja sobrescrever?"}), 409

        else:
            # Single file (filme / desenho / anime)
            url = (script.get("url") or "").strip()
            if not url:
                return jsonify({"message": "Campo 'url' obrigatório para tipo filme"}), 400

            dest_path = os.path.join(base_path, nome)
            if os.path.exists(dest_path) and any(os.scandir(dest_path)) and not force:
                return jsonify({"message": f"'{nome}' já existe. Deseja sobrescrever?"}), 409

            if not force:
                tipo_base = tipo if tipo in ("filme", "desenho", "anime") else "filme"
                outros = cross_pool_exists(nome, tipo_base, storage_root)
                if outros:
                    return jsonify({"message": f"'{nome}' já existe em {', '.join(outros)}. Deseja baixar mesmo assim em {storage_root}?"}), 409

            os.makedirs(dest_path, exist_ok=True)
            links_file = os.path.join(dest_path, "links.txt")
            with open(links_file, "w", encoding="utf-8") as f:
                f.write(url)

            cursor.execute("""
                INSERT INTO jobs (series_name, tipo, temporada, caminho, status, sobrescrever)
                VALUES (%s, 'filme', NULL, %s, 'queued', %s)
            """, (nome, dest_path, bool(force)))
            queued = 1

        conn.commit()

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({"message": f"Script executado! {queued} job(s) adicionado(s) à fila.", "queued": queued})


# =========================================================
# ======================= WEBHOOKS ========================
# =========================================================


@app.route("/webhooks")
@admin_required
def webhooks_page():
    return render_template("webhooks.html")


# =========================================================
# ======================== UPDATES ========================
# =========================================================

@app.route("/updates")
@login_required
def updates_page():
    return render_template("updates.html")


@app.route("/api/updates", methods=["GET"])
def listar_updates():
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT id, title, body, created_at FROM updates ORDER BY created_at DESC")
        rows = cursor.fetchall()
        for r in rows:
            if hasattr(r["created_at"], "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
        return jsonify(rows)
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()


@app.route("/api/webhooks", methods=["GET"])
def listar_webhooks():
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM webhooks ORDER BY criado_em DESC")
        data = cursor.fetchall()
        
        # Converte datas para string pra não bugar o jsonify do flask e envia os webhooks
        for row in data:
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
                    
        return jsonify(data)
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()


@app.route("/api/webhooks", methods=["POST"])
def criar_webhook():
    try:
        data = request.json or {}
        nome = (data.get("nome") or "").strip()
        tipo = (data.get("tipo") or "generic").strip().lower()
        if not nome:
            return {"error": "Nome é obrigatório"}, 400
        if tipo not in ("generic", "whatsapp"):
            return {"error": "Tipo inválido"}, 400

        url_val = None
        config_val = None

        if tipo == "generic":
            url_val = (data.get("url") or "").strip()
            if not url_val:
                return {"error": "URL é obrigatória para webhook genérico"}, 400
        else:  # whatsapp
            cfg = data.get("config") or {}
            server = (cfg.get("server") or "").strip().rstrip("/")
            instance = (cfg.get("instance") or "").strip()
            api_key = (cfg.get("api_key") or "").strip()
            destinos = [d.strip() for d in (cfg.get("destinos") or []) if d and d.strip()]
            if not server or not instance or not api_key or not destinos:
                return {"error": "WhatsApp: server, instance, api_key e ao menos 1 destino são obrigatórios"}, 400
            url_val = f"{server}/message/sendText/{instance}"
            config_val = json.dumps({
                "server": server,
                "instance": instance,
                "api_key": api_key,
                "destinos": destinos,
            })

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO webhooks (nome, url, tipo, config, ativo) VALUES (%s,%s,%s,%s,TRUE)",
            (nome, url_val, tipo, config_val),
        )
        conn.commit()
        return {"status": "ok"}
    except Exception as e:
        if 'conn' in locals() and conn: conn.rollback()
        return {"error": str(e)}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()


@app.route("/api/webhooks/<int:id>/toggle", methods=["POST"])
def toggle_webhook(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE webhooks SET ativo = NOT ativo WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.route("/api/webhooks/<int:id>/delete", methods=["POST"])
def delete_webhook(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM webhooks WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.route("/api/webhooks/<int:id>", methods=["PUT"])
def editar_webhook(id):
    try:
        data = request.json or {}
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT tipo FROM webhooks WHERE id=%s", (id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return {"error": "Webhook não encontrado"}, 404
        tipo = row["tipo"] or "generic"

        nome = (data.get("nome") or "").strip()
        if not nome:
            return {"error": "Nome é obrigatório"}, 400

        if tipo == "generic":
            url_val = (data.get("url") or "").strip()
            if not url_val:
                return {"error": "URL é obrigatória"}, 400
            cursor.execute(
                "UPDATE webhooks SET nome=%s, url=%s WHERE id=%s",
                (nome, url_val, id),
            )
        else:  # whatsapp
            cfg = data.get("config") or {}
            server = (cfg.get("server") or "").strip().rstrip("/")
            instance = (cfg.get("instance") or "").strip()
            api_key = (cfg.get("api_key") or "").strip()
            destinos = [d.strip() for d in (cfg.get("destinos") or []) if d and d.strip()]
            if not server or not instance or not api_key or not destinos:
                return {"error": "WhatsApp: server, instance, api_key e ao menos 1 destino são obrigatórios"}, 400
            url_val = f"{server}/message/sendText/{instance}"
            config_val = json.dumps({
                "server": server,
                "instance": instance,
                "api_key": api_key,
                "destinos": destinos,
            })
            cursor.execute(
                "UPDATE webhooks SET nome=%s, url=%s, config=%s WHERE id=%s",
                (nome, url_val, config_val, id),
            )

        conn.commit()
        return {"status": "ok"}
    except Exception as e:
        if 'conn' in locals() and conn: conn.rollback()
        return {"error": str(e)}, 500
    finally:
        if 'cursor' in locals() and cursor: cursor.close()
        if 'conn' in locals() and conn: conn.close()


@app.route("/api/webhooks/<int:id>/test", methods=["POST"])
def testar_webhook(id):
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM webhooks WHERE id=%s", (id,))
    hook = cursor.fetchone()

    if not hook:
        cursor.close()
        conn.close()
        return {"error": "Webhook não encontrado"}, 404

    tipo = hook.get("tipo") or "generic"
    msg = "🧪 Teste enviado pelo MDM"
    last_code = 0
    any_success = False

    try:
        if tipo == "whatsapp":
            cfg = hook.get("config") or {}
            server = (cfg.get("server") or "").rstrip("/")
            instance = cfg.get("instance") or ""
            api_key = cfg.get("api_key") or ""
            destinos = cfg.get("destinos") or []
            if not server or not instance or not api_key or not destinos:
                return {"error": "Config WhatsApp incompleta"}, 400
            endpoint = f"{server}/message/sendText/{instance}"
            headers = {"apikey": api_key, "Content-Type": "application/json"}
            erros = []
            for numero in destinos:
                try:
                    r = requests.post(endpoint, json={"number": numero, "text": msg}, headers=headers, timeout=10)
                    last_code = r.status_code
                    if r.status_code < 400:
                        any_success = True
                    else:
                        erros.append(f"{numero}: HTTP {r.status_code} {r.text[:200]}")
                except Exception as e:
                    erros.append(f"{numero}: {e}")
            status = "success" if any_success else "error"
            cursor.execute(
                "UPDATE webhooks SET ultima_execucao=%s, ultimo_status=%s, ultimo_codigo=%s WHERE id=%s",
                (datetime.now(), status, last_code, id),
            )
            conn.commit()
            if any_success:
                return {"status": "ok", "code": last_code, "erros": erros}
            return {"error": "Falha ao enviar para todos os destinos", "detalhes": erros}, 502
        else:
            response = requests.post(hook["url"], json={"content": msg}, timeout=5)
            status = "success" if response.status_code < 400 else "error"
            cursor.execute(
                "UPDATE webhooks SET ultima_execucao=%s, ultimo_status=%s, ultimo_codigo=%s WHERE id=%s",
                (datetime.now(), status, response.status_code, id),
            )
            conn.commit()
            return {"status": "ok", "code": response.status_code}

    except Exception as e:
        return {"error": str(e)}, 500

    finally:
        cursor.close()
        conn.close()


# =========================================================
# ======================= DOWNLOADS =======================
# =========================================================

@app.route("/downloads")
@login_required
def downloads():
    return render_template("downloads.html")


@app.route("/api/jobs")
def api_jobs():
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM jobs ORDER BY id DESC")
    jobs = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(jobs)


@app.route("/api/progress/<int:job_id>")
def job_progress(job_id):
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT caminho FROM jobs WHERE id=%s", (job_id,))
    job = cursor.fetchone()
    cursor.close()
    conn.close()

    if not job:
        return {"progress": 0}

    log_file = os.path.join(job["caminho"], "download.log")
    if not os.path.exists(log_file):
        return {"progress": 0}

    with open(log_file, "r", errors="ignore") as f:
        for line in reversed(f.readlines()):
            if "%" in line:
                try:
                    # Tenta capturar o número do percentual garantindo que seja um float válido
                    match = re.search(r'\[download\]\s+([\d\.]+)%', line)
                    if match:
                        return {"progress": float(match.group(1))}
                    
                    # Fallback log antigo/ytdlp:
                    percent_str = line.split("%")[0].split()[-1]
                    return {"progress": float(percent_str)}
                except:
                    continue

    return {"progress": 0}


@app.route("/api/cancel/<int:job_id>", methods=["POST"])
def cancel_job(job_id):
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT pid FROM jobs WHERE id=%s", (job_id,))
    job = cursor.fetchone()

    if job and job["pid"]:
        try:
            os.kill(job["pid"], 9)
        except:
            pass

    cursor.execute("""
        UPDATE jobs
        SET status='cancelled',
            finished_at=NOW(),
            pid=NULL
        WHERE id=%s
    """, (job_id,))

    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "cancelled"}


# =========================================================
# ======================= USUÁRIOS ========================
# =========================================================

@app.route("/usuarios")
@admin_required
def usuarios_page():
    return render_template("usuarios.html")


@app.route("/api/users", methods=["GET"])
@admin_required
def listar_users():
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT id, username, role, ativo, created_at FROM users ORDER BY id ASC")
    data = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(data)


@app.route("/api/users", methods=["POST"])
@admin_required
def criar_user():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "user")

    if not username or not password:
        return {"error": "Usuário e senha são obrigatórios"}, 400

    if role not in ("admin", "user"):
        return {"error": "Role inválida"}, 400

    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Verifica duplicado
    cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        return {"error": "Usuário já existe"}, 409

    cursor.execute(
        "INSERT INTO users (username, password, role, ativo) VALUES (%s, %s, %s, true)",
        (username, hash_password(password), role)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.route("/api/users/<int:id>/role", methods=["POST"])
@admin_required
def change_user_role(id):
    data = request.json
    new_role = data.get("role", "user")
    if new_role not in ("admin", "user"):
        return {"error": "Role inválida"}, 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role=%s WHERE id=%s", (new_role, id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.route("/api/users/<int:id>/toggle", methods=["POST"])
@admin_required
def toggle_user(id):
    # Não permite desativar a si mesmo
    if session.get("user_id") == id:
        return {"error": "Não é possível desativar seu próprio usuário"}, 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET ativo = NOT ativo WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.route("/api/users/<int:id>/password", methods=["POST"])
@admin_required
def change_user_password(id):
    data = request.json
    new_password = data.get("password", "")
    if not new_password or len(new_password) < 3:
        return {"error": "Senha deve ter pelo menos 3 caracteres"}, 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password=%s WHERE id=%s", (hash_password(new_password), id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


@app.route("/api/users/<int:id>/delete", methods=["POST"])
@admin_required
def delete_user(id):
    # Não permite deletar a si mesmo
    if session.get("user_id") == id:
        return {"error": "Não é possível excluir seu próprio usuário"}, 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok"}


# =========================================================
# ======================= M3U SERVERS =====================
# =========================================================

import unicodedata as _ud

def _slugify(s):
    """Slug ASCII lowercase, sem espaços nem caracteres especiais."""
    if not s:
        return ""
    s = _ud.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "server"


def _refresh_server_file_stats(server_id, filepath):
    """Atualiza linhas/tamanho/atualizado_em do server no DB depois de salvar o arquivo."""
    try:
        size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        lines = 0
        if os.path.exists(filepath):
            with open(filepath, "rb") as f:
                for _ in f: lines += 1
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE m3u_servers SET linhas=%s, tamanho_bytes=%s, atualizado_em=NOW() WHERE id=%s",
            (lines, size, server_id),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[m3u] erro atualizando stats do server {server_id}: {e}")


@app.route("/m3u")
@admin_required
def m3u_page():
    return render_template("m3u.html")


@app.route("/api/m3u_servers", methods=["GET"])
@login_required
def listar_m3u_servers():
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, nome, slug, filename, linhas, tamanho_bytes, ativo, atualizado_em, criado_em FROM m3u_servers ORDER BY id")
        rows = cur.fetchall()
        for r in rows:
            for k in ("atualizado_em", "criado_em"):
                if r.get(k) and hasattr(r[k], "isoformat"):
                    r[k] = r[k].isoformat()
        cur.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/m3u_servers", methods=["POST"])
@admin_required
def criar_m3u_server():
    """Cria um server novo. Recebe multipart: 'nome' + 'file' (.txt/.m3u)."""
    nome = (request.form.get("nome") or "").strip()
    if not nome:
        return {"error": "Nome obrigatório"}, 400
    if "file" not in request.files or request.files["file"].filename == "":
        return {"error": "Arquivo M3U obrigatório"}, 400
    f = request.files["file"]
    if not (f.filename.endswith(".txt") or f.filename.endswith(".m3u")):
        return {"error": "Formato inválido (use .txt ou .m3u)"}, 400

    slug = _slugify(nome)
    filename_rel = f"data/servers/{slug}.txt"
    filepath = os.path.join(BASE_DIR, filename_rel)

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM m3u_servers WHERE nome=%s OR slug=%s", (nome, slug))
        if cur.fetchone():
            cur.close(); conn.close()
            return {"error": "Já existe um server com esse nome"}, 409

        f.save(filepath)
        size = os.path.getsize(filepath)
        lines = 0
        with open(filepath, "rb") as fh:
            for _ in fh: lines += 1

        cur.execute(
            "INSERT INTO m3u_servers (nome, slug, filename, linhas, tamanho_bytes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (nome, slug, filename_rel, lines, size),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        indexer.reindex(server_id=new_id)
        return {"status": "ok", "id": new_id}
    except Exception as e:
        if 'conn' in locals() and conn: conn.rollback()
        return {"error": str(e)}, 500


@app.route("/api/m3u_servers/<int:sid>/upload", methods=["POST"])
@admin_required
def upload_m3u_server(sid):
    """Substitui o arquivo M3U de um server existente."""
    if "file" not in request.files or request.files["file"].filename == "":
        return {"error": "Arquivo M3U obrigatório"}, 400
    f = request.files["file"]
    if not (f.filename.endswith(".txt") or f.filename.endswith(".m3u")):
        return {"error": "Formato inválido (use .txt ou .m3u)"}, 400

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM m3u_servers WHERE id=%s", (sid,))
    server = cur.fetchone()
    cur.close()
    conn.close()
    if not server:
        return {"error": "Server não encontrado"}, 404

    filepath = os.path.join(BASE_DIR, server["filename"])
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    f.save(filepath)
    _refresh_server_file_stats(sid, filepath)
    indexer.reindex(server_id=sid)
    return {"status": "ok"}


@app.route("/api/m3u_servers/<int:sid>", methods=["PUT"])
@admin_required
def renomear_m3u_server(sid):
    data = request.json or {}
    nome = (data.get("nome") or "").strip()
    if not nome:
        return {"error": "Nome obrigatório"}, 400
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE m3u_servers SET nome=%s WHERE id=%s", (nome, sid))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/m3u_servers/<int:sid>/toggle", methods=["POST"])
@admin_required
def toggle_m3u_server(sid):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE m3u_servers SET ativo = NOT ativo WHERE id=%s RETURNING ativo", (sid,))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if row is None:
        return {"error": "Server não encontrado"}, 404
    # Re-sincroniza o indexer (vai dropar entries do server se desativado)
    indexer.reindex()
    return {"status": "ok", "ativo": row[0]}


@app.route("/api/m3u_servers/<int:sid>", methods=["DELETE"])
@admin_required
def deletar_m3u_server(sid):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT filename FROM m3u_servers WHERE id=%s", (sid,))
    server = cur.fetchone()
    if not server:
        cur.close(); conn.close()
        return {"error": "Server não encontrado"}, 404
    cur.execute("DELETE FROM m3u_servers WHERE id=%s", (sid,))
    conn.commit()
    cur.close()
    conn.close()
    # Apaga o arquivo do disco
    try:
        filepath = os.path.join(BASE_DIR, server["filename"])
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"[m3u] erro removendo arquivo do server {sid}: {e}")
    indexer.drop_server(sid)
    return {"status": "ok"}


@app.route("/api/m3u_servers/<int:sid>/reindex", methods=["POST"])
@admin_required
def reindex_m3u_server(sid):
    stats = indexer.reindex(server_id=sid)
    return {"status": "ok", "stats": stats}


# Endpoint legado — mantido pra retrocompat (sobrescreve o server "Principal").
@app.route("/api/upload_m3u", methods=["POST"])
@admin_required
def upload_m3u_legacy():
    if "file" not in request.files or request.files["file"].filename == "":
        return {"error": "Nenhum arquivo enviado"}, 400
    f = request.files["file"]
    if not (f.filename.endswith(".txt") or f.filename.endswith(".m3u")):
        return {"error": "Formato inválido"}, 400

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, filename FROM m3u_servers WHERE slug='principal' LIMIT 1")
    server = cur.fetchone()
    cur.close()
    conn.close()
    if not server:
        return {"error": "Server 'principal' não encontrado — cadastre um server primeiro"}, 404

    filepath = os.path.join(BASE_DIR, server["filename"])
    f.save(filepath)
    _refresh_server_file_stats(server["id"], filepath)
    stats = indexer.reindex(server_id=server["id"])
    return {"status": "ok", "stats": stats}


# =========================================================
# ===================== LIMPEZA STORAGE ===================
# =========================================================

import threading

CLEANUP_STATE = {
    "running": False,
    "deleted_count": 0,
    "freed_space_mb": 0.0,
    "deleted_dirs": 0,
    "current_file": "",
    "error": None
}

@app.route("/cleanup")
@admin_required
def cleanup_page():
    return render_template("cleanup.html")

def _bg_cleanup_task():
    global CLEANUP_STATE
    pools = list_storage_roots()
    deleted_count = 0
    freed_space_bytes = 0
    deleted_dirs_count = 0

    target_names = ["download.log", "links.txt"]
    target_ext = ".part"

    try:
        if not pools:
            CLEANUP_STATE["error"] = "Nenhum storage pool encontrado"
            CLEANUP_STATE["running"] = False
            return

        for base_path in pools:
            if not os.path.exists(base_path):
                continue

            pool_label = os.path.basename(base_path)
            CLEANUP_STATE["current_file"] = f"[{pool_label}] iniciando..."

            for root, dirs, files in os.walk(base_path, topdown=False):
                for file in files:
                    CLEANUP_STATE["current_file"] = f"[{pool_label}] {file}"
                    if file in target_names or file.endswith(target_ext):
                        filepath = os.path.join(root, file)
                        try:
                            size = os.path.getsize(filepath)
                            os.remove(filepath)
                            deleted_count += 1
                            freed_space_bytes += size
                        except Exception as e:
                            print(f"[Cleanup] Erro ao deletar {filepath}: {e}")

                if root != base_path:
                    try:
                        if not os.listdir(root):
                            os.rmdir(root)
                            deleted_dirs_count += 1
                    except Exception as e:
                        print(f"[Cleanup] Erro ao deletar pasta vazia {root}: {e}")

        freed_space_mb = round(freed_space_bytes / (1024 * 1024), 2)
        CLEANUP_STATE["deleted_count"] = deleted_count
        CLEANUP_STATE["freed_space_mb"] = freed_space_mb
        CLEANUP_STATE["deleted_dirs"] = deleted_dirs_count
    except Exception as e:
        CLEANUP_STATE["error"] = str(e)
    finally:
        CLEANUP_STATE["running"] = False

@app.route("/api/run_cleanup", methods=["POST"])
@admin_required
def run_cleanup():
    global CLEANUP_STATE
    if CLEANUP_STATE["running"]:
        return {"status": "ok", "message": "Limpeza já está em andamento"}

    CLEANUP_STATE.update({
        "running": True,
        "deleted_count": 0,
        "freed_space_mb": 0.0,
        "deleted_dirs": 0,
        "current_file": "Iniciando verificação...",
        "error": None
    })
    
    thread = threading.Thread(target=_bg_cleanup_task)
    thread.daemon = True
    thread.start()
    
    return {"status": "ok"}

@app.route("/api/cleanup_status", methods=["GET"])
@admin_required
def cleanup_status():
    global CLEANUP_STATE
    return jsonify(CLEANUP_STATE)

# =========================================================


# =========================================================
# =================== QBITTORRENT =========================
# =========================================================

QBIT_URL   = "http://10.0.1.157:8080"
QBIT_USER  = "admin"
QBIT_PASS  = "adminadmin"
QBIT_PATHS = [
    "/mnt/media/filmes",
    "/mnt/media/series",
    "/mnt/media2/animes",
    "/mnt/media2/animeseries",
    "/mnt/media2/desenhos",
    "/mnt/media2/desenhoseries",
]

_qbit_sess = None

def _qbit_login():
    global _qbit_sess
    import requests as _r
    s = _r.Session()
    resp = s.post(f"{QBIT_URL}/api/v2/auth/login",
                  data={"username": QBIT_USER, "password": QBIT_PASS}, timeout=10)
    if resp.text not in ("Ok.", "Ok"):
        raise Exception(f"qBittorrent login falhou: {resp.text}")
    _qbit_sess = s

def _qbit(method, path, **kwargs):
    global _qbit_sess
    if _qbit_sess is None:
        _qbit_login()
    r = getattr(_qbit_sess, method)(f"{QBIT_URL}{path}", timeout=15, **kwargs)
    if r.status_code == 403:
        _qbit_login()
        r = getattr(_qbit_sess, method)(f"{QBIT_URL}{path}", timeout=15, **kwargs)
    return r

@app.route("/torrents")
@login_required
def torrents():
    return render_template("torrents.html", save_paths=QBIT_PATHS)

@app.route("/api/qbit/torrents")
@login_required
def api_qbit_list():
    try:
        return jsonify(_qbit("get", "/api/v2/torrents/info").json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/qbit/add", methods=["POST"])
@login_required
def api_qbit_add():
    body      = request.get_json() or {}
    magnet    = body.get("magnetUrl", "").strip()
    save_path = body.get("savePath", "")
    if not magnet:
        return jsonify({"error": "magnetUrl obrigatorio"}), 400
    try:
        r = _qbit("post", "/api/v2/torrents/add",
                  data={"urls": magnet, "savepath": save_path})
        return jsonify({"ok": True, "result": r.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/qbit/pause/<torrent_hash>", methods=["POST"])
@login_required
def api_qbit_pause(torrent_hash):
    try:
        _qbit("post", "/api/v2/torrents/pause", data={"hashes": torrent_hash})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/qbit/resume/<torrent_hash>", methods=["POST"])
@login_required
def api_qbit_resume(torrent_hash):
    try:
        _qbit("post", "/api/v2/torrents/resume", data={"hashes": torrent_hash})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/qbit/delete/<torrent_hash>", methods=["POST"])
@login_required
def api_qbit_delete(torrent_hash):
    body      = request.get_json() or {}
    del_files = str(body.get("deleteFiles", False)).lower()
    try:
        _qbit("post", "/api/v2/torrents/delete",
              data={"hashes": torrent_hash, "deleteFiles": del_files})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3070, debug=True, use_reloader=False)