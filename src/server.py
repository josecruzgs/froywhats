#!/usr/bin/env python3
"""
Servidor webhook de WhatsApp para el agente de Froy.

- GET  /webhook  -> verificación que pide Meta (hub.challenge).
- POST /webhook  -> recibe mensajes entrantes, los pasa por el agente y responde.
- Cada conversación guarda su metadato georreferenciado en data/registros.jsonl.

Correr:  python src/server.py   (escucha en el puerto 8000)
Requiere en .env: ANTHROPIC_API_KEY, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
"""
import os, json, sys, time, datetime, threading, tempfile, sqlite3
import requests
from flask import Flask, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agente      # carga .env, system prompt y base de conocimiento
import humanizar   # ritmo humano: lectura, escritura por globos, pausas
import green_api   # WhatsApp vía Green API (no oficial, por QR)
import transcribir_audio  # transcripción de notas de voz (faster-whisper, CPU)

MENSAJE_AUDIO_FALLIDO = "no pude escuchar bien tu audio 🙏 ¿me lo puedes escribir?"

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

# Historial corto por número, persistido en SQLite (no en memoria: gunicorn corre
# varios workers como procesos separados, y un dict en memoria no se comparte entre
# ellos ni sobrevive a un restart).
CONV_DB = os.path.join(DATA, "conversaciones.db")

def _db():
    con = sqlite3.connect(CONV_DB, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS historial (
        numero TEXT PRIMARY KEY,
        datos TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS contactos (
        numero TEXT PRIMARY KEY,
        nombre TEXT,
        ciudad TEXT,
        colonia TEXT,
        tema TEXT,
        postura TEXT,
        canal TEXT,
        mensajes INTEGER NOT NULL DEFAULT 0,
        primera_vez TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )""")
    con.execute("PRAGMA journal_mode=WAL")
    return con

def cargar_historial(numero):
    con = _db()
    try:
        row = con.execute("SELECT datos FROM historial WHERE numero=?", (numero,)).fetchone()
        return json.loads(row[0]) if row else []
    finally:
        con.close()

def guardar_historial(numero, hist):
    con = _db()
    try:
        con.execute(
            "INSERT INTO historial (numero, datos, actualizado_en) VALUES (?,?,?) "
            "ON CONFLICT(numero) DO UPDATE SET datos=excluded.datos, actualizado_en=excluded.actualizado_en",
            (numero, json.dumps(hist, ensure_ascii=False), datetime.datetime.utcnow().isoformat()))
        con.commit()
    finally:
        con.close()

def cargar_contacto(numero):
    """Perfil ya conocido de este número (nombre/ciudad/colonia), para dárselo de contexto al agente."""
    con = _db()
    try:
        row = con.execute(
            "SELECT nombre, ciudad, colonia, tema, postura, mensajes FROM contactos WHERE numero=?",
            (numero,)).fetchone()
        if not row:
            return None
        return {"nombre": row[0], "ciudad": row[1], "colonia": row[2],
                "tema": row[3], "postura": row[4], "mensajes": row[5]}
    finally:
        con.close()

def actualizar_contacto(numero, meta, canal):
    """Guarda/actualiza el perfil del contacto con lo nuevo de este mensaje, sin perder
    lo que ya se sabía si este mensaje no lo repite."""
    meta = meta or {}
    con = _db()
    try:
        prev = con.execute(
            "SELECT nombre, ciudad, colonia, tema, postura, mensajes, primera_vez FROM contactos WHERE numero=?",
            (numero,)).fetchone()
        nombre = meta.get("nombre") or (prev[0] if prev else None)
        ciudad = meta.get("municipio") or (prev[1] if prev else None)
        colonia = meta.get("colonia") or (prev[2] if prev else None)
        tema = meta.get("tema") or (prev[3] if prev else None)
        postura = meta.get("postura") or (prev[4] if prev else None)
        mensajes = (prev[5] if prev else 0) + 1
        primera_vez = prev[6] if prev else datetime.datetime.utcnow().isoformat()
        ahora = datetime.datetime.utcnow().isoformat()
        con.execute("""
            INSERT INTO contactos (numero,nombre,ciudad,colonia,tema,postura,canal,mensajes,primera_vez,actualizado_en)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(numero) DO UPDATE SET
                nombre=excluded.nombre, ciudad=excluded.ciudad, colonia=excluded.colonia,
                tema=excluded.tema, postura=excluded.postura, canal=excluded.canal,
                mensajes=excluded.mensajes, actualizado_en=excluded.actualizado_en
        """, (numero, nombre, ciudad, colonia, tema, postura, canal, mensajes, primera_vez, ahora))
        con.commit()
    finally:
        con.close()

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

def _transcribir_desde_url(url, headers=None):
    """Descarga un audio de una URL y lo transcribe. Lanza excepción si algo falla."""
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    mime = r.headers.get("Content-Type", "")
    ext = ".ogg"
    for candidato in (".mp3", ".m4a", ".wav", ".amr", ".aac", ".flac", ".webm"):
        if candidato.strip(".") in mime:
            ext = candidato
            break
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(r.content)
        tmp.close()
        return transcribir_audio.transcribir(tmp.name)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass

def _descargar_media_meta(media_id):
    """Resuelve un media_id de WhatsApp Cloud API a su URL temporal de descarga."""
    r = requests.get(f"https://graph.facebook.com/v21.0/{media_id}",
                      headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
    r.raise_for_status()
    return r.json()["url"]

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

def procesar(numero, message_id, texto=None, audio_media_id=None):
    """Hace todo el trabajo pesado (agente + ritmo humano + envío). Corre en un hilo
    aparte para que el webhook le responda a Meta de inmediato."""
    try:
        leido_y_escribiendo(message_id)  # palomita azul + 'escribiendo…' mientras piensa
        if audio_media_id and not texto:
            try:
                media_url = _descargar_media_meta(audio_media_id)
                texto = _transcribir_desde_url(media_url, headers={"Authorization": f"Bearer {TOKEN}"})
            except Exception as e:
                print("Error transcribiendo audio Meta:", e, flush=True)
                texto = None
            if not texto or not texto.strip():
                enviar_whatsapp(numero, MENSAJE_AUDIO_FALLIDO)
                return
        hist = cargar_historial(numero)
        contacto = cargar_contacto(numero)
        t0 = time.time()
        salida = agente.responder(texto, hist, contacto=contacto)
        pensado = time.time() - t0  # ya se mostró 'escribiendo…' este rato
        responder_humano(numero, message_id, texto, salida, ya_pensado=pensado)
        guardar_registro(numero, texto, salida)
        actualizar_contacto(numero, salida.get("meta"), "whatsapp")
        hist += [{"role": "user", "content": texto},
                 {"role": "assistant", "content": salida.get("respuesta", "")}]
        guardar_historial(numero, hist[-12:])
    except Exception as e:
        print("Error procesando mensaje:", e, flush=True)

@app.post("/webhook")
def recibir():
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    tipo_msg = msg.get("type")
                    if tipo_msg not in ("text", "audio"):
                        continue
                    mid = msg["id"]
                    if mid in _vistos:      # reintento de Meta -> ya lo procesamos
                        continue
                    _vistos.add(mid)
                    if len(_vistos) > 5000:
                        _vistos.clear()
                    # procesar en segundo plano y devolver 200 ya mismo
                    if tipo_msg == "text":
                        threading.Thread(target=procesar,
                            args=(msg["from"], mid, msg["text"]["body"]), daemon=True).start()
                    else:  # audio
                        threading.Thread(target=procesar,
                            args=(msg["from"], mid), kwargs={"audio_media_id": msg["audio"]["id"]},
                            daemon=True).start()
    except Exception as e:
        print("Error procesando webhook:", e, flush=True)
    return "OK", 200  # responder rápido para que Meta no reintente

# ---------- Green API (WhatsApp no oficial por QR) ----------
_green_vistos = set()

def procesar_green(chat_id, numero, mensaje):
    """Trabajo pesado para un mensaje de Green API (agente + ritmo humano + envío)."""
    try:
        origen = "green"
        if mensaje["tipo"] == "audio":
            try:
                texto = _transcribir_desde_url(mensaje["url"])
            except Exception as e:
                print("Error transcribiendo audio Green:", e, flush=True)
                texto = None
            if not texto or not texto.strip():
                green_api.enviar(chat_id, MENSAJE_AUDIO_FALLIDO)
                return
            origen = "green-audio"
        else:
            texto = mensaje["texto"]
        hist = cargar_historial(numero)
        contacto = cargar_contacto(numero)
        salida = agente.responder(texto, hist, contacto=contacto)
        respuesta = salida.get("respuesta", "")
        globos = humanizar.dividir_en_globos(respuesta)
        for i, g in enumerate(globos):
            if HUMANIZAR:
                espera = humanizar.tiempo_escritura(g)
                green_api.escribiendo(chat_id, ms=int(espera * 1000))
                time.sleep(espera)
            green_api.enviar(chat_id, g)
            if HUMANIZAR and i < len(globos) - 1:
                time.sleep(humanizar.pausa_entre_globos())
        guardar_registro(numero, texto, salida, origen=origen)
        actualizar_contacto(numero, salida.get("meta"), origen)
        hist += [{"role": "user", "content": texto},
                 {"role": "assistant", "content": respuesta}]
        guardar_historial(numero, hist[-12:])
    except Exception as e:
        print("Error procesando mensaje Green:", e, flush=True)

@app.post("/green-webhook")
def green_webhook():
    data = request.get_json(silent=True) or {}
    try:
        p = green_api.parse_incoming(data)
        if p:
            chat, numero, mensaje = p
            mid = data.get("idMessage") or (chat + "|" + json.dumps(mensaje, sort_keys=True))
            if mid not in _green_vistos:
                _green_vistos.add(mid)
                if len(_green_vistos) > 5000:
                    _green_vistos.clear()
                threading.Thread(target=procesar_green,
                                 args=(chat, numero, mensaje), daemon=True).start()
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
