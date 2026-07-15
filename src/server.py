#!/usr/bin/env python3
"""
Servidor webhook de WhatsApp para el agente de Froy.

- GET  /webhook  -> verificación que pide Meta (hub.challenge).
- POST /webhook  -> recibe mensajes entrantes, los pasa por el agente y responde.
- Cada conversación guarda su metadato georreferenciado en data/registros.jsonl.

Correr:  python src/server.py   (escucha en el puerto 8000)
Requiere en .env: ANTHROPIC_API_KEY, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
"""
import os, json, sys, time, datetime, threading
import requests
from flask import Flask, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agente      # carga .env, system prompt y base de conocimiento
import humanizar   # ritmo humano: lectura, escritura por globos, pausas
import green_api   # WhatsApp vía Green API (no oficial, por QR)

HUMANIZAR = os.environ.get("HUMANIZAR", "1") != "0"
_vistos = set()    # ids de mensajes ya procesados (evita duplicados por reintentos de Meta)

TOKEN     = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_ID  = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY    = os.environ.get("WHATSAPP_VERIFY_TOKEN", "froy-verify-2026")
GRAPH_URL = f"https://graph.facebook.com/v21.0/{PHONE_ID}/messages"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)
REGISTROS = os.path.join(DATA, "registros.jsonl")

app = Flask(__name__)

# Historial corto por número (en memoria; para producción usar una base de datos).
historiales = {}

def _post(payload):
    return requests.post(GRAPH_URL,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json=payload)

def enviar_whatsapp(numero, texto):
    """Envía un mensaje de texto al ciudadano vía Cloud API."""
    r = _post({"messaging_product": "whatsapp", "to": numero,
               "type": "text", "text": {"body": texto}})
    if r.status_code >= 300:
        print("Error al enviar:", r.status_code, r.text, flush=True)
    return r.ok

def leido_y_escribiendo(message_id):
    """Marca el mensaje como leído (palomita azul) y muestra 'escribiendo…'.
    Best-effort: si la cuenta no tiene la función, se ignora el error."""
    try:
        _post({"messaging_product": "whatsapp", "status": "read",
               "message_id": message_id, "typing_indicator": {"type": "text"}})
    except Exception as e:
        print("typing indicator no disponible:", e, flush=True)

def responder_humano(numero, message_id, texto, salida, ya_pensado=0.0):
    """Reproduce el ritmo humano: leer -> escribir globo -> pausa -> siguiente globo.

    `ya_pensado` = segundos que el agente tardó en responder (durante los cuales ya se mostró
    'escribiendo…'). Ese tiempo se descuenta de las primeras esperas para que NO se sume encima
    y la respuesta no se sienta lenta."""
    respuesta = salida.get("respuesta", "")
    if not HUMANIZAR:
        enviar_whatsapp(numero, respuesta)
        return
    leido_y_escribiendo(message_id)
    deuda = ya_pensado  # tiempo a "perdonar" de las próximas esperas
    for paso in humanizar.plan_de_envio(texto, respuesta):
        espera = paso["seg"]
        if deuda > 0:
            descuento = min(deuda, espera)
            espera -= descuento
            deuda -= descuento
        time.sleep(espera)
        if paso["accion"] == "escribir":
            enviar_whatsapp(numero, paso["texto"])
            leido_y_escribiendo(message_id)  # re-activa 'escribiendo…' para el siguiente globo

def guardar_registro(numero, mensaje, salida, origen="whatsapp"):
    """Persiste el metadato georreferenciado para el CRM / segmentación."""
    meta = salida.get("meta", {})
    registro = {
        "fecha": datetime.datetime.utcnow().isoformat(),
        "origen": origen,
        "numero": numero,
        "mensaje": mensaje,
        "respuesta": salida.get("respuesta", ""),
        **{k: meta.get(k) for k in
           ("tipo", "tema", "municipio", "colonia", "audiencia", "escalar", "motivo_escalamiento")},
    }
    with open(REGISTROS, "a") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")

@app.get("/webhook")
def verificar():
    """Meta verifica el webhook con un challenge."""
    if (request.args.get("hub.mode") == "subscribe" and
            request.args.get("hub.verify_token") == VERIFY):
        return request.args.get("hub.challenge", ""), 200
    return "Verificación fallida", 403

def procesar(numero, message_id, texto):
    """Hace todo el trabajo pesado (agente + ritmo humano + envío). Corre en un hilo
    aparte para que el webhook le responda a Meta de inmediato."""
    try:
        leido_y_escribiendo(message_id)  # palomita azul + 'escribiendo…' mientras piensa
        hist = historiales.get(numero, [])
        t0 = time.time()
        salida = agente.responder(texto, hist)
        pensado = time.time() - t0  # ya se mostró 'escribiendo…' este rato
        responder_humano(numero, message_id, texto, salida, ya_pensado=pensado)
        guardar_registro(numero, texto, salida)
        hist += [{"role": "user", "content": texto},
                 {"role": "assistant", "content": salida.get("respuesta", "")}]
        historiales[numero] = hist[-12:]
    except Exception as e:
        print("Error procesando mensaje:", e, flush=True)

@app.post("/webhook")
def recibir():
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    if msg.get("type") != "text":
                        continue
                    mid = msg["id"]
                    if mid in _vistos:      # reintento de Meta -> ya lo procesamos
                        continue
                    _vistos.add(mid)
                    if len(_vistos) > 5000:
                        _vistos.clear()
                    # procesar en segundo plano y devolver 200 ya mismo
                    threading.Thread(target=procesar,
                        args=(msg["from"], mid, msg["text"]["body"]), daemon=True).start()
    except Exception as e:
        print("Error procesando webhook:", e, flush=True)
    return "OK", 200  # responder rápido para que Meta no reintente

# ---------- Green API (WhatsApp no oficial por QR) ----------
_green_vistos = set()

def procesar_green(chat_id, numero, texto):
    """Trabajo pesado para un mensaje de Green API (agente + ritmo humano + envío)."""
    try:
        hist = historiales.get(numero, [])
        salida = agente.responder(texto, hist)
        respuesta = salida.get("respuesta", "")
        globos = humanizar.dividir_en_globos(respuesta)
        for i, g in enumerate(globos):
            if HUMANIZAR:
                time.sleep(humanizar.tiempo_escritura(g))
            green_api.enviar(chat_id, g)
            if HUMANIZAR and i < len(globos) - 1:
                time.sleep(humanizar.pausa_entre_globos())
        guardar_registro(numero, texto, salida, origen="green")
        hist += [{"role": "user", "content": texto},
                 {"role": "assistant", "content": respuesta}]
        historiales[numero] = hist[-12:]
    except Exception as e:
        print("Error procesando mensaje Green:", e, flush=True)

@app.post("/green-webhook")
def green_webhook():
    data = request.get_json(silent=True) or {}
    try:
        p = green_api.parse_incoming(data)
        if p:
            chat, numero, texto = p
            mid = data.get("idMessage") or (chat + "|" + texto)
            if mid not in _green_vistos:
                _green_vistos.add(mid)
                if len(_green_vistos) > 5000:
                    _green_vistos.clear()
                threading.Thread(target=procesar_green,
                                 args=(chat, numero, texto), daemon=True).start()
    except Exception as e:
        print("Error webhook Green:", e, flush=True)
    return "OK", 200

@app.get("/")
def salud():
    return "Agente de Froy activo 🟢", 200

if __name__ == "__main__":
    faltan = [k for k in ("ANTHROPIC_API_KEY", "WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID")
              if not os.environ.get(k)]
    if faltan:
        print("⚠ Faltan variables en .env:", ", ".join(faltan))
        print("  El servidor arranca igual, pero no podrá responder hasta que las pongas.")
    print("Servidor en http://localhost:8000  (webhook en /webhook)")
    app.run(host="0.0.0.0", port=8000)
