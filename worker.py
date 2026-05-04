import time
import os
import subprocess
import threading
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import psycopg2.extras
from db import get_connection

# =========================================================
# ====================== CONFIGURAÇÕES ====================
# =========================================================

CHECK_INTERVAL = 3          # Tempo entre verificações da fila (segundos)
START_DELAY = 3             # Delay inicial antes de processar
YTDLP_PATH = "/usr/bin/yt-dlp"
MAX_RETRIES = 3             # Tentativas por episódio
CHUNK_SIZE = 8192           # Tamanho dos chunks para download streaming
CANCEL_CHECK_BYTES = 1024 * 1024  # Verifica cancel a cada 1MB

# Limite global de downloads simultâneos (qualquer categoria)
MAX_SIMULTANEOS = 5

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}

# Contador global thread-safe
_lock = threading.Lock()
_ativos_total = 0


def get_categoria(caminho):
    """Determina a categoria do job pelo caminho de destino."""
    c = caminho or ''
    if '/mnt/media/animeseries'   in c: return 'animeseries'
    if '/mnt/media/animes'        in c: return 'animes'
    if '/mnt/media/desenhosseries' in c: return 'desenhosseries'
    if '/mnt/media/desenhos'      in c: return 'desenhos'
    if '/mnt/media/series'        in c: return 'series'
    return 'filmes'


# =========================================================
# ================ VERIFICAÇÃO DE CANCEL ==================
# =========================================================

def is_job_cancelled(job_id):
    """
    Verifica no banco se o job foi cancelado.
    Usa sua própria conexão (thread-safe).
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM jobs WHERE id=%s", (job_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row and row[0] == "cancelled"
    except Exception:
        return False


# =========================================================
# =================== DOWNLOAD DIRETO ====================
# =========================================================

def download_direct(url, filepath, log, job_id=None):
    """
    Faz download direto via HTTP usando requests com streaming.
    Segue redirecionamentos automaticamente.
    Verifica cancelamento a cada 1MB baixado.
    Retorna:
        True        → sucesso
        False       → falhou
        "cancelled" → cancelado pelo usuário
    """
    try:
        response = requests.get(
            url,
            headers=DOWNLOAD_HEADERS,
            stream=True,
            allow_redirects=True,
            timeout=(15, 300)
        )

        if response.status_code not in (200, 206):
            log.write(f"[download_direct] HTTP {response.status_code} para {url}\n")
            log.flush()
            return False

        content_type = response.headers.get("Content-Type", "")
        content_length = response.headers.get("Content-Length")

        valid_types = ("video/", "application/octet-stream", "application/mp4", "text/plain", "binary/octet-stream")
        if content_type and not any(t in content_type for t in valid_types):
            # Relaxa a verificação gravando apenas o Warning em log, prosseguindo com download. 
            log.write(f"[download_direct] Warning: Content-Type incomum: {content_type}, mas tentando baixar...\n")
            log.flush()

        total_size = int(content_length) if content_length else None
        downloaded = 0
        last_log_percent = -1
        bytes_since_cancel_check = 0

        log.write(f"[download_direct] Iniciando download: {url}\n")
        if total_size:
            log.write(f"[download_direct] Tamanho total: {total_size / (1024*1024):.1f} MB\n")
        log.flush()

        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    bytes_since_cancel_check += len(chunk)

                    # ── Verifica cancelamento a cada 1MB ──
                    if job_id and bytes_since_cancel_check >= CANCEL_CHECK_BYTES:
                        bytes_since_cancel_check = 0
                        if is_job_cancelled(job_id):
                            log.write(f"[download_direct] 🛑 Download cancelado pelo usuário\n")
                            log.flush()
                            response.close()
                            f.close()
                            if os.path.exists(filepath):
                                os.remove(filepath)
                            return "cancelled"

                    # Loga progresso a cada 5%
                    if total_size and total_size > 0:
                        percent = int((downloaded / total_size) * 100)
                        if percent >= last_log_percent + 5:
                            last_log_percent = percent
                            log.write(f"[download] {percent}% de {total_size / (1024*1024):.1f}MB\n")
                            log.flush()

        # Verifica se o arquivo foi criado com tamanho mínimo de 1 MB
        MIN_SIZE = 1 * 1024 * 1024  # 1 MB
        if os.path.exists(filepath) and os.path.getsize(filepath) >= MIN_SIZE:
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            log.write(f"[download_direct] ✅ Concluído: {size_mb:.1f} MB\n")
            log.flush()
            return True
        else:
            size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            log.write(f"[download_direct] ❌ Arquivo muito pequeno ou não criado ({size} bytes)\n")
            log.flush()
            if os.path.exists(filepath):
                os.remove(filepath)
            return False

    except requests.exceptions.Timeout:
        log.write(f"[download_direct] ❌ Timeout ao conectar/baixar\n")
        log.flush()
        if os.path.exists(filepath):
            os.remove(filepath)
        return False
    except Exception as e:
        log.write(f"[download_direct] ❌ Erro: {str(e)}\n")
        log.flush()
        if os.path.exists(filepath):
            os.remove(filepath)
        return False


# =========================================================
# ======================== WEBHOOK ========================
# =========================================================

def send_webhook(message):
    """
    Envia mensagem para todos webhooks ativos.
    Atualiza status de execução no banco.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT * FROM webhooks WHERE ativo=true")
        hooks = cursor.fetchall()

        for hook in hooks:
            try:
                response = requests.post(
                    hook["url"],
                    json={"content": message},
                    timeout=5
                )

                status = "success" if response.status_code < 400 else "error"

                cursor.execute("""
                    UPDATE webhooks
                    SET ultima_execucao=%s,
                        ultimo_status=%s,
                        ultimo_codigo=%s
                    WHERE id=%s
                """, (
                    datetime.now(),
                    status,
                    response.status_code,
                    hook["id"]
                ))

            except Exception:
                cursor.execute("""
                    UPDATE webhooks
                    SET ultima_execucao=%s,
                        ultimo_status='error',
                        ultimo_codigo=0
                    WHERE id=%s
                """, (
                    datetime.now(),
                    hook["id"]
                ))

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[webhook] Erro ao enviar webhook: {e}")


# =========================================================
# ======================== FILA ===========================
# =========================================================

CATEGORIA_PATH = {
    'filmes':          '/mnt/media/filmes/',
    'series':          '/mnt/media/series/',
    'desenhos':        '/mnt/media/desenhos/',
    'desenhosseries':  '/mnt/media/desenhosseries/',
    'animes':          '/mnt/media/animes/',
    'animeseries':     '/mnt/media/animeseries/',
}

def get_next_jobs(cursor, slots_disponiveis):
    """
    Retorna os próximos jobs da fila (FIFO) respeitando o limite global.
    """
    if slots_disponiveis <= 0:
        return []
    cursor.execute("""
        SELECT * FROM jobs
        WHERE status='queued'
        ORDER BY id ASC
        LIMIT %s
    """, (slots_disponiveis,))
    return cursor.fetchall()


# =========================================================
# ====================== EXECUÇÃO JOB =====================
# =========================================================

def start_job(job):
    """
    Executa o download de todos links de um job.
    Retornos possíveis:
        True        → sucesso completo
        False       → falhou
        "cancelled" → cancelado manualmente
    """

    caminho = job["caminho"]
    links_file = os.path.join(caminho, "links.txt")
    log_file = os.path.join(caminho, "download.log")

    # 🔍 Se não existir arquivo de links, falha
    if not os.path.exists(links_file):
        return False

    with open(links_file, "r", encoding="utf-8") as f:
        linhas = [l.strip() for l in f.readlines() if l.strip()]

    if not linhas:
        return False

    with open(log_file, "a") as log:

        for linha in linhas:

            # ==========================================
            # VERIFICA CANCELAMENTO EM TEMPO REAL
            # ==========================================
            if is_job_cancelled(job["id"]):
                log.write(f"[worker] 🛑 Job cancelado antes de iniciar próximo episódio\n")
                log.flush()
                return "cancelled"

            # ==========================================
            # SEPARA NOME E URL
            # ==========================================
            if "|" in linha:
                nome, url = linha.split("|", 1)
            else:
                nome = job["series_name"]
                url = linha

            filename = f"{nome}.mp4"
            filepath = os.path.join(caminho, filename)

            # ==========================================
            # SE ARQUIVO JÁ EXISTE → PULA OU APAGA (SOBRESCREVER)
            # ==========================================
            if os.path.exists(filepath):
                if not job.get("sobrescrever", False):
                    continue
                else:
                    try:
                        os.remove(filepath)
                        log.write(f"[worker] Arquivo antigo removido para sobrescrever: {filename}\n")
                        log.flush()
                    except Exception as e:
                        log.write(f"[worker] Erro ao remover arquivo existente: {e}\n")
                        log.flush()

            sucesso_download = False

            # ==========================================
            # TENTATIVAS DE DOWNLOAD
            # ==========================================
            for tentativa in range(MAX_RETRIES):

                # ── 1) Tenta download direto via requests ──
                log.write(f"\n[worker] Tentativa {tentativa + 1}/{MAX_RETRIES} — {filename}\n")
                log.flush()

                result = download_direct(url, filepath, log, job_id=job["id"])

                if result == "cancelled":
                    return "cancelled"

                if result is True:
                    sucesso_download = True
                    break

                # ── 2) Fallback: tenta via yt-dlp se disponível ──
                if os.path.exists(YTDLP_PATH):
                    log.write(f"[worker] Tentando fallback via yt-dlp...\n")
                    log.flush()

                    command = [
                        YTDLP_PATH,
                        url,
                        "--no-playlist",
                        "--user-agent", DOWNLOAD_HEADERS["User-Agent"],
                        "--continue",
                        "--no-overwrites",
                        "-o", filename
                    ]

                    process = subprocess.Popen(
                        command,
                        cwd=caminho,
                        stdout=log,
                        stderr=log
                    )

                    # Salva PID no banco
                    try:
                        conn_pid = get_connection()
                        cur_pid = conn_pid.cursor()
                        cur_pid.execute("""
                            UPDATE jobs SET pid=%s WHERE id=%s
                        """, (process.pid, job["id"]))
                        conn_pid.commit()
                        cur_pid.close()
                        conn_pid.close()
                    except Exception:
                        pass

                    process.wait()

                    # Limpa PID
                    try:
                        conn_pid = get_connection()
                        cur_pid = conn_pid.cursor()
                        cur_pid.execute("""
                            UPDATE jobs SET pid=NULL WHERE id=%s
                        """, (job["id"],))
                        conn_pid.commit()
                        cur_pid.close()
                        conn_pid.close()
                    except Exception:
                        pass

                    if process.returncode == 0 and os.path.exists(filepath):
                        sucesso_download = True
                        break

                time.sleep(2)

            if not sucesso_download:
                return False

    return True


# =========================================================
# ================ PROCESSAR JOB (THREAD) =================
# =========================================================

def process_job(job):
    """
    Processa um job individual (executado dentro de uma thread).
    Gerencia contadores de slots e trata resultado final.
    """
    global _ativos_total
    tipo = job["tipo"]
    job_id = job["id"]

    print(f"📥 [{tipo.upper()}] Iniciando: {job['series_name']} (ID: {job_id})")

    # ── Marca como downloading ──
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE jobs
            SET status='downloading', finished_at=NULL
            WHERE id=%s
        """, (job_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[worker] Erro ao marcar downloading: {e}")

    # ── Executa download ──
    try:
        if tipo in ("serie", "filme"):
            resultado = start_job(job)
        else:
            resultado = False
    except Exception as e:
        print(f"[worker] Erro fatal no job {job_id}: {e}")
        resultado = False

    # ── Tratamento final ──
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 🟢 SUCESSO
        if resultado is True:
            cursor.execute("""
                UPDATE jobs
                SET status='completed', finished_at=NOW(), pid=NULL
                WHERE id=%s
            """, (job_id,))
            conn.commit()

            if tipo == "serie":
                send_webhook(f"✅ Download finalizado: {job['series_name']} - Temporada {job['temporada']}")
            else:
                send_webhook(f"🎬 Filme finalizado: {job['series_name']}")

            print(f"✅ [{tipo.upper()}] Concluído: {job['series_name']}")

        # 🟡 CANCELADO
        elif resultado == "cancelled":
            cursor.execute("""
                UPDATE jobs
                SET status='cancelled', finished_at=NOW(), pid=NULL
                WHERE id=%s
            """, (job_id,))
            conn.commit()

            if tipo == "serie":
                send_webhook(f"🛑 Download cancelado: {job['series_name']} - Temporada {job['temporada']}")
            else:
                send_webhook(f"🛑 Filme cancelado: {job['series_name']}")

            print(f"🛑 [{tipo.upper()}] Cancelado: {job['series_name']}")

        # 🔴 FALHOU
        else:
            cursor.execute("""
                UPDATE jobs
                SET status='failed', finished_at=NOW(), pid=NULL, retries=retries+1
                WHERE id=%s
            """, (job_id,))
            conn.commit()

            if tipo == "serie":
                send_webhook(f"❌ Download falhou: {job['series_name']} - Temporada {job['temporada']}")
            else:
                send_webhook(f"❌ Filme falhou: {job['series_name']}")

            print(f"❌ [{tipo.upper()}] Falhou: {job['series_name']}")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"[worker] Erro ao finalizar job {job_id}: {e}")

    # ── Libera slot ──
    with _lock:
        _ativos_total -= 1


# =========================================================
# ======================== LOOP ===========================
# =========================================================

def main():
    """
    Loop principal do worker.
    Gerencia pool de threads e distribui jobs.
    """
    global _ativos_total

    print(f"🚀 MDM Worker iniciado — máximo {MAX_SIMULTANEOS} downloads simultâneos")

    # Ao reiniciar, jobs presos como 'downloading' voltam para 'queued'
    # e seus arquivos .mp4 parciais são removidos para evitar falso "completed"
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, caminho FROM jobs WHERE status='downloading'")
        stuck = cur.fetchall()
        if stuck:
            for job in stuck:
                caminho = job["caminho"]
                if caminho and os.path.isdir(caminho):
                    for f in os.listdir(caminho):
                        fpath = os.path.join(caminho, f)
                        # Remove apenas arquivos parciais: .part ou .mp4 < 1MB
                        is_part = f.endswith(".mp4.part")
                        is_small_mp4 = f.endswith(".mp4") and os.path.getsize(fpath) < 1 * 1024 * 1024
                        if is_part or is_small_mp4:
                            try:
                                os.remove(fpath)
                                print(f"   🗑️  Removido arquivo parcial: {f}")
                            except Exception:
                                pass
            cur.execute("UPDATE jobs SET status='queued' WHERE status='downloading'")
            conn.commit()
            print(f"   ♻️  {len(stuck)} job(s) retomados (arquivos parciais removidos)")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"   ⚠️  Erro ao resetar jobs travados: {e}")

    executor = ThreadPoolExecutor(max_workers=MAX_SIMULTANEOS)

    while True:

        time.sleep(CHECK_INTERVAL)

        try:
            with _lock:
                slots = MAX_SIMULTANEOS - _ativos_total

            if slots <= 0:
                continue

            conn = get_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            jobs = get_next_jobs(cursor, slots)

            if jobs:
                for job in jobs:
                    with _lock:
                        if _ativos_total >= MAX_SIMULTANEOS:
                            break
                        _ativos_total += 1
                    executor.submit(process_job, job)

            cursor.close()
            conn.close()

        except Exception as e:
            print(f"[worker] Erro no loop principal: {e}")
            time.sleep(5)


# =========================================================

if __name__ == "__main__":
    main()