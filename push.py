"""Web Push helper — reusável por app.py (HTTP endpoints) e worker.py (notificações de downloads)."""
import os
import json
import psycopg2.extras
from db import get_connection

try:
    from pywebpush import webpush, WebPushException
    _AVAILABLE = True
except Exception as e:
    _AVAILABLE = False
    print(f"[push] pywebpush não disponível: {e}")

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")


def send_web_push(user_id, payload):
    """Dispara notificação push pra subscriptions de um usuário.

    user_id: string com username, ou "*" para broadcast a todos.
    payload: {title, body, url, icon} — todos opcionais exceto title.

    Subscriptions expiradas (HTTP 404/410) são removidas do DB.
    Retorna o nº de envios bem-sucedidos.
    """
    if not _AVAILABLE or not (VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY):
        return 0
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if user_id == "*":
            cur.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions")
        else:
            cur.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=%s", (user_id,))
        subs = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[push] erro carregando subs: {e}")
        return 0

    if not subs:
        return 0

    body_json = json.dumps({
        "title": payload.get("title") or "MDM Titan",
        "body":  payload.get("body")  or "",
        "url":   payload.get("url")   or "/dashboard",
        "icon":  payload.get("icon")  or "/static/icons/icon-192.png",
    })

    sent = 0
    dead_ids = []
    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=body_json,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=10,
            )
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
            if code in (404, 410):
                dead_ids.append(s["id"])
            else:
                print(f"[push] falha ({code}) pra {s['endpoint'][:60]}: {e}")
        except Exception as e:
            print(f"[push] erro inesperado: {e}")

    if dead_ids:
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM push_subscriptions WHERE id = ANY(%s)", (dead_ids,))
            conn.commit()
            cur.close()
            conn.close()
            print(f"[push] {len(dead_ids)} subscription(s) expiradas removidas")
        except Exception as e:
            print(f"[push] erro removendo subs expiradas: {e}")

    return sent
