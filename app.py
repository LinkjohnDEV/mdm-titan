from flask import Flask, render_template, request, redirect, session, jsonify
import os
import shutil
import hashlib
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
DATA_FILE = os.path.join(BASE_DIR, "data", "dados.txt")
SERIES_BASE = "/mnt/media/series"
FILMES_BASE = "/mnt/media/filmes"
DESENHOS_BASE = "/mnt/media/desenhos"
DESENHOS_SERIES_BASE = "/mnt/media/desenhosseries"
ANIMES_BASE = "/mnt/media/animes"
ANIMES_SERIES_BASE = "/mnt/media/animeseries"
MEDIA_BASE = "/mnt/media"

# Inicializa indexador de busca
indexer.init(DATA_FILE)


# =========================================================
# ======================== LOGIN ==========================
# =========================================================

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


@app.route("/buscar_series")
def buscar_series():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return {"results": []}
    resultados = indexer.search_series(termo)
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
    serie_path = os.path.join(SERIES_BASE, nome)
    season_path = os.path.join(serie_path, f"Season {temporada}")
    
    # Se a pasta da temporada já existir e "force" não for verdadeiro
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
    queued = 0
    exists_seasons = []

    conn = get_connection()
    cursor = conn.cursor()

    for s in seasons:
        temporada = s.get("season")
        episodios = s.get("episodes")
        if not temporada or not episodios:
            continue

        serie_path = os.path.join(SERIES_BASE, nome)
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
            (series_name, tipo, temporada, caminho,
             status, sobrescrever, total_episodios, episodios_baixados)
            VALUES (%s, 'serie', %s, %s, 'queued', %s, %s, 0)
        """, (nome, temporada, season_path, bool(force), len(episodios)))
        queued += 1

    conn.commit()
    cursor.close()
    conn.close()

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
    resultados = indexer.search_filmes(query)
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
    pasta = os.path.join(FILMES_BASE, nome_seguro)
    
    if os.path.exists(pasta) and any(os.scandir(pasta)) and not force:
        return {"error": "exists", "message": "Este filme já existe no servidor. Deseja sobrescrevê-lo e baixar novamente?"}, 409

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
    return render_template("desenhos.html")


@app.route("/search_desenho_filme")
def search_desenho_filme():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])
    return jsonify(indexer.search_desenhos_filmes(query))


@app.route("/search_desenho_serie")
def search_desenho_serie():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return {"results": []}
    return {"results": indexer.search_desenhos_series(termo)}


@app.route("/add_desenho_filme", methods=["POST"])
def add_desenho_filme():
    data = request.json
    nome = data.get("name")
    link = data.get("link")

    if not nome or not link:
        return {"message": "Dados inválidos"}, 400

    nome_seguro = re.sub(r'[\\/*?:"<>|]', "", nome).strip()
    force = data.get("force", False)
    pasta = os.path.join(DESENHOS_BASE, nome_seguro)

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
    serie_path = os.path.join(DESENHOS_SERIES_BASE, nome)
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
        serie_path = os.path.join(DESENHOS_SERIES_BASE, nome)
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
    return render_template("animes.html")


@app.route("/search_anime_filme")
def search_anime_filme():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])
    return jsonify(indexer.search_animes_filmes(query))


@app.route("/search_anime_serie")
def search_anime_serie():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return {"results": []}
    return {"results": indexer.search_animes_series(termo)}


@app.route("/add_anime_filme", methods=["POST"])
def add_anime_filme():
    data = request.json
    nome = data.get("name")
    link = data.get("link")

    if not nome or not link:
        return {"message": "Dados inválidos"}, 400

    nome_seguro = re.sub(r'[\\/*?:"<>|]', "", nome).strip()
    force = data.get("force", False)
    pasta = os.path.join(ANIMES_BASE, nome_seguro)

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
    serie_path = os.path.join(ANIMES_SERIES_BASE, nome)
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
        serie_path = os.path.join(ANIMES_SERIES_BASE, nome)
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

def _safe_path(relative):
    """Resolve um caminho relativo dentro de MEDIA_BASE com segurança."""
    full = os.path.realpath(os.path.join(MEDIA_BASE, relative.lstrip("/")))
    if not full.startswith(os.path.realpath(MEDIA_BASE)):
        return None
    return full


@app.route("/api/storage/mkdir", methods=["POST"])
@login_required
def storage_mkdir():
    data = request.json
    path = _safe_path(data.get("path", ""))
    if not path:
        return {"error": "Caminho inválido"}, 400
    if os.path.exists(path):
        return {"error": "Pasta já existe"}, 409
    os.makedirs(path)
    return {"status": "ok"}


@app.route("/api/storage/rename", methods=["POST"])
@login_required
def storage_rename():
    data = request.json
    src  = _safe_path(data.get("path", ""))
    new_name = data.get("new_name", "").strip()
    if not src or not new_name:
        return {"error": "Parâmetros inválidos"}, 400
    if "/" in new_name or "\\" in new_name:
        return {"error": "Nome inválido"}, 400
    if not os.path.exists(src):
        return {"error": "Origem não encontrada"}, 404
    dst = os.path.join(os.path.dirname(src), new_name)
    dst_safe = _safe_path(os.path.relpath(dst, MEDIA_BASE))
    if not dst_safe:
        return {"error": "Destino inválido"}, 400
    if os.path.exists(dst_safe):
        return {"error": "Já existe um item com esse nome"}, 409
    os.rename(src, dst_safe)
    return {"status": "ok"}


@app.route("/api/storage/folders")
@login_required
def storage_folders():
    """Lista todas as pastas de primeiro nível dentro de /mnt/media para o seletor de destino."""
    try:
        folders = sorted([
            d for d in os.listdir(MEDIA_BASE)
            if os.path.isdir(os.path.join(MEDIA_BASE, d))
        ])
        return {"folders": folders}
    except Exception as e:
        return {"folders": [], "error": str(e)}


@app.route("/api/storage/move", methods=["POST"])
@login_required
def storage_move():
    data = request.json
    src = _safe_path(data.get("src", ""))
    # dst_dir é a PASTA de destino; o item será movido para dentro dela
    dst_dir = _safe_path(data.get("dst_dir", ""))
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


# =========================================================
# ======================= STORAGE =========================
# =========================================================

from flask import send_file

@app.route("/storage")
@app.route("/storage/<path:subpath>")
@login_required
def storage(subpath=""):

    current_path = os.path.join(MEDIA_BASE, subpath)

    if not os.path.abspath(current_path).startswith(os.path.abspath(MEDIA_BASE)):
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
            items.append({
                "name": item,
                "is_dir": is_dir,
                "size": size_str
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
        parent=parent
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
    data = request.json
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify([])
    resultados = indexer.search_all(query, limit=100)
    return jsonify(resultados)


@app.route("/api/storage/browse")
@login_required
def storage_browse():
    """Lista subpastas de um caminho relativo para o seletor de destino do Coringa."""
    rel = request.args.get("path", "")
    current = os.path.realpath(os.path.join(MEDIA_BASE, rel.lstrip("/")))
    if not current.startswith(os.path.realpath(MEDIA_BASE)):
        return jsonify({"error": "Caminho inválido"}), 400
    try:
        folders = sorted([
            d for d in os.listdir(current)
            if os.path.isdir(os.path.join(current, d))
        ])
        rel_current = os.path.relpath(current, MEDIA_BASE)
        if rel_current == ".":
            rel_current = ""
        parent = os.path.relpath(os.path.dirname(current), MEDIA_BASE) if rel_current else None
        if parent == ".":
            parent = ""
        return jsonify({"path": rel_current, "parent": parent, "folders": folders})
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

    dest_abs = os.path.realpath(os.path.join(MEDIA_BASE, dest_rel.lstrip("/")))
    if not dest_abs.startswith(os.path.realpath(MEDIA_BASE)):
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

    # Determine base path: use dest if provided, otherwise use tipo default
    TYPE_BASES = {
        "serie":         SERIES_BASE,
        "filme":         FILMES_BASE,
        "filmes_batch":  FILMES_BASE,
        "desenho":       DESENHOS_BASE,
        "anime":         ANIMES_BASE,
        "desenho_serie": DESENHOS_SERIES_BASE,
        "anime_serie":   ANIMES_SERIES_BASE,
    }
    if tipo not in TYPE_BASES:
        return jsonify({"message": f"Tipo '{tipo}' não reconhecido. Use: {', '.join(TYPE_BASES)}"}), 400

    if dest_rel:
        dest_abs = os.path.realpath(os.path.join(MEDIA_BASE, dest_rel.lstrip("/")))
        if not dest_abs.startswith(os.path.realpath(MEDIA_BASE)):
            return jsonify({"message": "Destino inválido"}), 400
        base_path = dest_abs
    else:
        base_path = TYPE_BASES[tipo]

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
        data = request.json
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO webhooks (nome, url, ativo) VALUES (%s,%s,TRUE)",
            (data["nome"], data["url"])
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


@app.route("/api/webhooks/<int:id>/test", methods=["POST"])
def testar_webhook(id):
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM webhooks WHERE id=%s", (id,))
    hook = cursor.fetchone()

    if not hook:
        return {"error": "Webhook não encontrado"}, 404

    try:
        response = requests.post(
            hook["url"],
            json={"content": "🧪 Teste enviado pelo MDM"},
            timeout=5
        )

        status = "success" if response.status_code < 400 else "error"

        cursor.execute("""
            UPDATE webhooks
            SET ultima_execucao=%s,
                ultimo_status=%s,
                ultimo_codigo=%s
            WHERE id=%s
        """, (datetime.now(), status, response.status_code, id))

        conn.commit()
        return {"status": "ok"}

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
        "INSERT INTO users (username, password, role, ativo) VALUES (%s, %s, %s, 1)",
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
# ======================= UPLOAD M3U ======================
# =========================================================

@app.route("/m3u")
@admin_required
def m3u_page():
    return render_template("m3u.html")

@app.route("/api/upload_m3u", methods=["POST"])
@admin_required
def upload_m3u():
    if 'file' not in request.files:
        return {"error": "Nenhum arquivo enviado"}, 400
    
    file = request.files['file']
    if file.filename == '':
        return {"error": "Nenhum arquivo selecionado"}, 400
        
    if file and (file.filename.endswith('.txt') or file.filename.endswith('.m3u')):
        filepath = os.path.join(BASE_DIR, "data", "dados.txt")
        file.save(filepath)
        
        # Trigger reindex
        stats = indexer.reindex()
        return {"status": "ok", "stats": stats}
    
    return {"error": "Formato inválido. Envie um arquivo .txt ou .m3u"}, 400


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
    base_path = "/mnt/media"
    deleted_count = 0
    freed_space_bytes = 0
    deleted_dirs_count = 0
    
    target_names = ["download.log", "links.txt"]
    target_ext = ".part"
    
    try:
        if not os.path.exists(base_path):
            CLEANUP_STATE["error"] = f"O diretório {base_path} não existe"
            CLEANUP_STATE["running"] = False
            return

        for root, dirs, files in os.walk(base_path, topdown=False):
            for file in files:
                CLEANUP_STATE["current_file"] = file
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3070, debug=True, use_reloader=False)