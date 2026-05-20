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
MAX_RETRIES = 6             # Tentativas por episódio (RESUME_FEATURE 2026-05-20: era 3; com resume cada retry avança)
MAX_PER_HOSTNAME = 1        # HOSTNAME_THROTTLE 2026-05-20: max downloads ativos por hostname (anti rate-limit da fonte)
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

# HOSTNAME_THROTTLE (2026-05-20): contador de downloads ativos por hostname
_active_per_hostname = {}  # {hostname: count}


def _extract_first_hostname(job):
    """HOSTNAME_THROTTLE: lê primeira URL do links.txt e retorna hostname.
    Retorna None se falhar — nesse caso o job roda sem throttle.
    """
    from urllib.parse import urlparse
    try:
        caminho = job.get("caminho") or ""
        # filmes guardam links.txt direto na pasta; séries na pasta da temporada
        candidates = [
            os.path.join(caminho, "links.txt"),
        ]
        for path in candidates:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    first = f.readline().strip()
                if not first:
                    return None
                # Pode ser "nome|url" (série) ou só "url" (filme)
                url = first.split("|", 1)[1] if "|" in first else first
                return urlparse(url.strip()).hostname
    except Exception:
        pass
    return None


_CATEGORIA_BY_FOLDER = {
    'animeseries':    'animeseries',
    'animes':         'animes',
    'desenhosseries': 'desenhosseries',
    'desenhos':       'desenhos',
    'series':         'series',
    'filmes':         'filmes',
}

def get_categoria(caminho):
    """Categoria do job pela 2ª parte do path (independe do storage pool).

    Ex: /mnt/media/animes/foo  -> animes
        /mnt/media2/series/bar -> series
        /mnt/media3/filmes/x   -> filmes
    """
    import re as _re
    m = _re.match(r'^/mnt/[^/]+/([^/]+)', caminho or '')
    if m:
        return _CATEGORIA_BY_FOLDER.get(m.group(1), 'filmes')
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

def _get_remote_size(url, log=None):
    """RESUME_FEATURE (2026-05-20): HEAD request pra descobrir tamanho remoto.

    Retorna int (bytes) ou None se falhar. Best-effort — falha em silêncio.
    Usado pra decidir se arquivo local está completo, parcial, ou precisa refazer.
    """
    try:
        r = requests.head(
            url,
            headers=DOWNLOAD_HEADERS,
            allow_redirects=True,
            timeout=15
        )
        if r.status_code in (200, 206):
            cl = r.headers.get("Content-Length")
            if cl:
                return int(cl)
    except Exception as e:
        if log:
            log.write(f"[_get_remote_size] HEAD falhou: {e}\n")
            log.flush()
    return None


def download_direct(url, filepath, log, job_id=None, resume_from=0):
    """
    Faz download direto via HTTP usando requests com streaming.
    Segue redirecionamentos automaticamente.
    Verifica cancelamento a cada 1MB baixado.

    RESUME_FEATURE (2026-05-20):
      - Se resume_from > 0, envia Range: bytes={resume_from}- e abre em append
      - 206 Partial Content → continua de onde parou
      - 200 OK quando pedimos Range → servidor ignorou; sobrescreve do zero
      - Em erros de rede (IncompleteRead/Timeout), NÃO deleta o arquivo parcial
        (o retry no worker tenta resumir a partir do tamanho atual)
      - Só deleta o parcial se ficou MUITO pequeno (< 1MB) ou se sucesso final

    Retorna:
        True        → sucesso
        False       → falhou (parcial preservado em filepath se houve progresso)
        "cancelled" → cancelado pelo usuário
    """
    headers = dict(DOWNLOAD_HEADERS)
    mode = "wb"
    initial_downloaded = 0

    # RESUME_FEATURE: pede Range se temos byte inicial
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
        log.write(f"[download_direct] Tentando resume a partir de {resume_from / (1024*1024):.1f} MB\n")
        log.flush()

    try:
        response = requests.get(
            url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(15, 300)
        )

        # RESUME_FEATURE: interpreta resposta conforme Range
        if resume_from > 0 and response.status_code == 206:
            mode = "ab"
            initial_downloaded = resume_from
            log.write(f"[download_direct] ✅ Resume aceito (206 Partial Content)\n")
            log.flush()
        elif resume_from > 0 and response.status_code == 200:
            log.write(f"[download_direct] Servidor ignorou Range, sobrescrevendo do zero\n")
            log.flush()
            mode = "wb"
            initial_downloaded = 0
        # RESUME_FEATURE_FIX (2026-05-20): 416 = pedimos byte além do fim do arquivo
        # Significa que o arquivo já contém todos os bytes (provavelmente completo)
        elif resume_from > 0 and response.status_code == 416:
            content_range = response.headers.get("Content-Range", "")
            remote_total = None
            if content_range.startswith("bytes */"):
                try:
                    remote_total = int(content_range.split("/")[-1])
                except ValueError:
                    pass

            current_local = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            if remote_total is not None:
                if current_local == remote_total:
                    log.write(f"[download_direct] ✅ Arquivo já completo conforme servidor ({remote_total / (1024*1024):.1f} MB)\n")
                    log.flush()
                    return True
                elif current_local > remote_total:
                    # Arquivo local maior que o remoto - truncar pro tamanho correto
                    log.write(f"[download_direct] ⚠️ Local {current_local / (1024*1024):.1f} MB > remoto {remote_total / (1024*1024):.1f} MB — truncando\n")
                    log.flush()
                    with open(filepath, "rb+") as ftrunc:
                        ftrunc.truncate(remote_total)
                    return True
                else:
                    # Inconsistente: local < remote mas 416 — algo estranho
                    log.write(f"[download_direct] ❌ Inconsistente: local {current_local} < remote {remote_total} mas 416\n")
                    log.flush()
                    return False
            else:
                # Sem Content-Range: assume completo (best effort)
                log.write(f"[download_direct] ✅ 416 sem Content-Range — assumindo completo\n")
                log.flush()
                return True
        elif response.status_code not in (200, 206):
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

        # RESUME_FEATURE: Content-Length em 206 é o que FALTA, não o total
        remaining = int(content_length) if content_length else None
        total_size = (initial_downloaded + remaining) if remaining else None
        downloaded = initial_downloaded
        last_log_percent = int((downloaded / total_size) * 100) - 1 if total_size else -1
        bytes_since_cancel_check = 0
        # RESUME_FEATURE_FIX (2026-05-20): limitar bytes escritos ao total esperado pra
        # evitar arquivos maiores que o remoto quando o servidor manda bytes extras.
        remaining_to_write = (total_size - downloaded) if total_size else None

        log.write(f"[download_direct] Iniciando download: {url}\n")
        if total_size:
            log.write(f"[download_direct] Tamanho total: {total_size / (1024*1024):.1f} MB\n")
        log.flush()

        with open(filepath, mode) as f:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    # RESUME_FEATURE_FIX: trunca chunk se vai passar do esperado
                    if remaining_to_write is not None:
                        if remaining_to_write <= 0:
                            log.write(f"[download_direct] ⚠️ Servidor mandou bytes extras — interrompendo escrita\n")
                            log.flush()
                            break
                        if len(chunk) > remaining_to_write:
                            chunk = chunk[:remaining_to_write]

                    f.write(chunk)
                    downloaded += len(chunk)
                    bytes_since_cancel_check += len(chunk)
                    if remaining_to_write is not None:
                        remaining_to_write -= len(chunk)

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
            # RESUME_FEATURE_FIX (2026-05-20): só considera completo se atingiu o total_size esperado
            if total_size is not None and os.path.getsize(filepath) < total_size:
                log.write(f"[download_direct] ⚠️ Incompleto: {size_mb:.1f} MB de {total_size / (1024*1024):.1f} MB esperado\n")
                log.flush()
                return False
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
        # RESUME_FEATURE: NÃO deleta o arquivo parcial — retry tenta resume
        partial = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        log.write(f"[download_direct] ❌ Timeout ao conectar/baixar (parcial preservado: {partial / (1024*1024):.1f} MB)\n")
        log.flush()
        return False
    except Exception as e:
        # RESUME_FEATURE: NÃO deleta o arquivo parcial — retry tenta resume
        partial = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        log.write(f"[download_direct] ❌ Erro: {str(e)} (parcial preservado: {partial / (1024*1024):.1f} MB)\n")
        log.flush()
        return False


# =========================================================
# ======================== WEBHOOK ========================
# =========================================================

def _dispatch_generic(hook, message):
    response = requests.post(
        hook["url"],
        json={"content": message},
        timeout=5,
    )
    return response.status_code


def _dispatch_whatsapp(hook, message):
    cfg = hook.get("config") or {}
    server = (cfg.get("server") or "").rstrip("/")
    instance = cfg.get("instance") or ""
    api_key = cfg.get("api_key") or ""
    destinos = cfg.get("destinos") or []

    if not server or not instance or not api_key or not destinos:
        raise ValueError("config whatsapp incompleta (server/instance/api_key/destinos)")

    endpoint = f"{server}/message/sendText/{instance}"
    headers = {"apikey": api_key, "Content-Type": "application/json"}

    last_code = 0
    any_success = False
    for numero in destinos:
        try:
            r = requests.post(
                endpoint,
                json={"number": numero, "text": message},
                headers=headers,
                timeout=10,
            )
            last_code = r.status_code
            if r.status_code < 400:
                any_success = True
        except Exception as e:
            print(f"[webhook][whatsapp] falha em {numero}: {e}")
            last_code = 0
    return last_code if any_success else (last_code or 500)


def send_webhook(message):
    """
    Envia mensagem para todos webhooks ativos.
    Suporta tipos: generic (Discord-like) e whatsapp (Evolution API).
    """
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT * FROM webhooks WHERE ativo=true")
        hooks = cursor.fetchall()

        for hook in hooks:
            try:
                tipo = hook.get("tipo") or "generic"
                if tipo == "whatsapp":
                    code = _dispatch_whatsapp(hook, message)
                else:
                    code = _dispatch_generic(hook, message)

                status = "success" if code and code < 400 else "error"
                cursor.execute(
                    """
                    UPDATE webhooks
                    SET ultima_execucao=%s, ultimo_status=%s, ultimo_codigo=%s
                    WHERE id=%s
                    """,
                    (datetime.now(), status, code, hook["id"]),
                )

            except Exception as e:
                print(f"[webhook] falha no hook {hook.get('id')}: {e}")
                cursor.execute(
                    """
                    UPDATE webhooks
                    SET ultima_execucao=%s, ultimo_status='error', ultimo_codigo=0
                    WHERE id=%s
                    """,
                    (datetime.now(), hook["id"]),
                )

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[webhook] Erro ao enviar webhook: {e}")


# =========================================================
# ======================== FILA ===========================
# =========================================================

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
            # SE ARQUIVO JÁ EXISTE → COMPLETO PULA, PARCIAL RESUME, SOBRESCREVER APAGA
            # ==========================================
            # RESUME_FEATURE (2026-05-20): antes pulava se arquivo existisse, mesmo parcial.
            # Agora usa HEAD pra ver tamanho remoto e decide: pular (completo), resume (parcial), refazer (sobrescrever/zerado).
            if os.path.exists(filepath):
                current_size = os.path.getsize(filepath)

                if job.get("sobrescrever", False):
                    try:
                        os.remove(filepath)
                        log.write(f"[worker] Arquivo antigo removido para sobrescrever: {filename}\n")
                        log.flush()
                    except Exception as e:
                        log.write(f"[worker] Erro ao remover arquivo existente: {e}\n")
                        log.flush()
                elif current_size == 0:
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                else:
                    # Tenta descobrir tamanho remoto via HEAD pra comparar
                    remote_size = _get_remote_size(url, log)

                    if remote_size and current_size == remote_size:
                        log.write(f"[worker] Já completo ({current_size / (1024*1024):.1f} MB), pulando\n")
                        log.flush()
                        continue
                    elif remote_size and current_size > remote_size:
                        # RESUME_FEATURE_FIX (2026-05-20): arquivo local maior que remoto - truncar
                        log.write(f"[worker] Local {current_size / (1024*1024):.1f} MB > remoto {remote_size / (1024*1024):.1f} MB — truncando\n")
                        log.flush()
                        try:
                            with open(filepath, "rb+") as ftrunc:
                                ftrunc.truncate(remote_size)
                        except Exception as e:
                            log.write(f"[worker] Erro ao truncar: {e}\n")
                            log.flush()
                        continue
                    elif remote_size:
                        log.write(f"[worker] Parcial detectado ({current_size / (1024*1024):.1f}/{remote_size / (1024*1024):.1f} MB) — tentando resume\n")
                        log.flush()
                        # Cai no retry loop abaixo, que vai usar resume_from = current_size
                    else:
                        # HEAD falhou: se >= 50MB, assume incompleto e tenta resume; senão, refaz
                        if current_size >= 50 * 1024 * 1024:
                            log.write(f"[worker] HEAD falhou; parcial existente ({current_size / (1024*1024):.1f} MB) — tentando resume\n")
                            log.flush()
                        else:
                            log.write(f"[worker] HEAD falhou; arquivo pequeno ({current_size} bytes) — refazendo\n")
                            log.flush()
                            try:
                                os.remove(filepath)
                            except Exception:
                                pass

            sucesso_download = False

            # ==========================================
            # TENTATIVAS DE DOWNLOAD
            # ==========================================
            for tentativa in range(MAX_RETRIES):

                # ── 1) Tenta download direto via requests ──
                log.write(f"\n[worker] Tentativa {tentativa + 1}/{MAX_RETRIES} — {filename}\n")
                log.flush()

                # RESUME_FEATURE (2026-05-20): se já tem parcial (de tentativa anterior OU pré-existente), tenta resumir
                resume_from = 0
                if os.path.exists(filepath):
                    resume_from = os.path.getsize(filepath)
                    if resume_from > 0 and tentativa > 0:
                        log.write(f"[worker] Continuando do parcial ({resume_from / (1024*1024):.1f} MB)\n")
                        log.flush()

                result = download_direct(url, filepath, log, job_id=job["id"], resume_from=resume_from)

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

            # RESUME_FEATURE (2026-05-20): se TODAS as tentativas falharam, remove o parcial residual
            if not sucesso_download:
                if os.path.exists(filepath):
                    try:
                        partial_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        log.write(f"[worker] Parcial removido após {MAX_RETRIES} tentativas falhadas ({partial_size / (1024*1024):.1f} MB)\n")
                        log.flush()
                    except Exception as e:
                        log.write(f"[worker] Erro ao limpar parcial: {e}\n")
                        log.flush()
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
        # HOSTNAME_THROTTLE (2026-05-20): libera vaga do hostname
        hostname = job.get("_hostname")
        if hostname and hostname in _active_per_hostname:
            _active_per_hostname[hostname] = max(0, _active_per_hostname[hostname] - 1)
            if _active_per_hostname[hostname] == 0:
                del _active_per_hostname[hostname]


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
                    # HOSTNAME_THROTTLE (2026-05-20): respeita limite por hostname.
                    # Se o hostname já está no limite, pula este job — ele fica em queued
                    # e tentamos de novo no próximo loop tick.
                    hostname = _extract_first_hostname(job)
                    with _lock:
                        if _ativos_total >= MAX_SIMULTANEOS:
                            break
                        if hostname and _active_per_hostname.get(hostname, 0) >= MAX_PER_HOSTNAME:
                            continue
                        if hostname:
                            _active_per_hostname[hostname] = _active_per_hostname.get(hostname, 0) + 1
                        _ativos_total += 1
                        job["_hostname"] = hostname  # pra liberar depois
                    executor.submit(process_job, job)

            cursor.close()
            conn.close()

        except Exception as e:
            print(f"[worker] Erro no loop principal: {e}")
            time.sleep(5)


# =========================================================

if __name__ == "__main__":
    main()