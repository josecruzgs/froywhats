#!/usr/bin/env python3
"""
Integración con Green API — WhatsApp NO oficial (se conecta escaneando un QR,
sin aprobación de Meta). Recibir y enviar mensajes.

Config en data/green_config.json (editable desde el dashboard) o por variables de entorno:
  GREEN_ID_INSTANCE, GREEN_API_TOKEN, GREEN_API_URL
"""
import os, json, requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_F = os.path.join(BASE, "data", "green_config.json")

def cargar_config():
    cfg = {"id_instance": os.environ.get("GREEN_ID_INSTANCE", ""),
           "api_token": os.environ.get("GREEN_API_TOKEN", ""),
           "api_url": os.environ.get("GREEN_API_URL", "https://api.green-api.com")}
    if os.path.exists(CONFIG_F):
        try:
            f = json.load(open(CONFIG_F))
            for k in cfg:
                if f.get(k):
                    cfg[k] = f[k]
        except Exception:
            pass
    return cfg

def guardar_config(id_instance, api_token, api_url=""):
    os.makedirs(os.path.dirname(CONFIG_F), exist_ok=True)
    json.dump({"id_instance": (id_instance or "").strip(),
               "api_token": (api_token or "").strip(),
               "api_url": ((api_url or "https://api.green-api.com").strip().rstrip("/"))},
              open(CONFIG_F, "w"))

def configurado():
    c = cargar_config()
    return bool(c["id_instance"] and c["api_token"])

def _base(c):
    return f"{c['api_url'].rstrip('/')}/waInstance{c['id_instance']}"

def estado():
    """getStateInstance -> {'stateInstance': 'authorized'} cuando el QR está escaneado."""
    c = cargar_config()
    if not configurado():
        return {"error": "sin configurar"}
    try:
        r = requests.get(f"{_base(c)}/getStateInstance/{c['api_token']}", timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def enviar(chat_id, texto):
    c = cargar_config()
    if not configurado():
        return False
    try:
        r = requests.post(f"{_base(c)}/sendMessage/{c['api_token']}",
                          json={"chatId": chat_id, "message": texto}, timeout=20)
        if not r.ok:
            print("Green API enviar:", r.status_code, r.text[:200], flush=True)
        return r.ok
    except Exception as e:
        print("Green API enviar error:", e, flush=True)
        return False

def escribiendo(chat_id, ms=5000):
    """Muestra el 'escribiendo…' en el chat por `ms` milisegundos (máx. 20000, Green API lo limita)."""
    c = cargar_config()
    if not configurado():
        return False
    try:
        r = requests.post(f"{_base(c)}/sendTyping/{c['api_token']}",
                          json={"chatId": chat_id, "typingTime": max(1000, min(20000, int(ms)))}, timeout=15)
        return r.ok
    except Exception as e:
        print("Green API escribiendo error:", e, flush=True)
        return False

def parse_incoming(data):
    """Extrae (chatId, numero, mensaje) de una notificación de Green API, o None.
    `mensaje` es {"tipo": "texto", "texto": ...} o {"tipo": "audio", "url": ..., "mime": ...}."""
    if data.get("typeWebhook") != "incomingMessageReceived":
        return None
    md = data.get("messageData", {})
    tipo = md.get("typeMessage")
    if tipo == "textMessage":
        mensaje = {"tipo": "texto", "texto": md.get("textMessageData", {}).get("textMessage", "")}
        if not mensaje["texto"]:
            return None
    elif tipo == "extendedTextMessage":
        mensaje = {"tipo": "texto", "texto": md.get("extendedTextMessageData", {}).get("text", "")}
        if not mensaje["texto"]:
            return None
    elif tipo in ("audioMessage", "pttMessage"):
        fmd = md.get("fileMessageData", {})
        mensaje = {"tipo": "audio", "url": fmd.get("downloadUrl", ""), "mime": fmd.get("mimeType", "")}
        if not mensaje["url"]:
            return None
    else:
        return None
    chat = data.get("senderData", {}).get("chatId", "")
    if not chat:
        return None
    # solo chats individuales (ignora grupos @g.us)
    if not chat.endswith("@c.us"):
        return None
    return chat, chat.split("@")[0], mensaje
