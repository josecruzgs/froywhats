#!/usr/bin/env python3
"""
Toma de control humano de una conversación ("modo operador").

Cuando el agente de IA no está contestando bien, un operador del panel puede tomar el
control de ese chat por MINUTOS_DEFAULT minutos. Mientras dura:
  - el webhook NO le pasa los mensajes al agente (solo los guarda),
  - el operador contesta a mano desde el modal de Explorar.
Al vencer el plazo el agente retoma solo, y lo hace CON CONTEXTO: los mensajes del
ciudadano y los del operador se guardan en el mismo `historial` que lee el agente, más
una nota de traspaso que se le entrega una sola vez.

El estado vive en la tabla `intervenciones` de data/conversaciones.db porque el panel
(dashboard.py) y el webhook (server.py) son procesos distintos: un dict en memoria no se
compartiría entre ellos ni sobreviviría a un restart.
"""
import os, sys, json, time, datetime, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import green_api
import tiempo       # reloj único: se guarda en UTC, el panel lo muestra en Tijuana

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
CONV_DB = os.path.join(DATA, "conversaciones.db")
REGISTROS = os.path.join(DATA, "registros.jsonl")

MINUTOS_DEFAULT = int(os.environ.get("INTERVENCION_MINUTOS", "30"))
MINUTOS_MAX = 180          # tope duro por si alguien extiende de más y se olvida

# Lock por número (compartido con server.py a través de la tabla `locks`): serializa el
# procesamiento de un mismo teléfono entre threads Y entre procesos de gunicorn.
LOCK_TTL_SEG = 120         # si un worker muere con el lock tomado, se abandona tras esto
LOCK_ESPERA_MAX_SEG = 90   # cuánto espera un mensaje a que termine el anterior del mismo número


# Los tres se delegan a tiempo.py para que el webhook, el panel y la bitácora midan con el
# mismo reloj. `_parse` devuelve siempre una fecha con zona (las guardadas antes del cambio
# no traen offset y se asumen UTC), así que se puede comparar con `_ahora()` sin reventar.
def _ahora():
    return tiempo.ahora()


def _iso(dt):
    return tiempo.iso(dt)


def _parse(s):
    return tiempo.parse(s)


def _db():
    con = sqlite3.connect(CONV_DB, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS historial (
        numero TEXT PRIMARY KEY,
        datos TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS locks (
        numero TEXT PRIMARY KEY,
        adquirido_en TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS intervenciones (
        numero TEXT PRIMARY KEY,
        operador TEXT NOT NULL,
        inicio TEXT NOT NULL,
        expira TEXT NOT NULL,
        cerrada_en TEXT,
        motivo_cierre TEXT,
        traspaso_avisado INTEGER NOT NULL DEFAULT 0
    )""")
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ---------- lock por número ----------
class _LockNumero:
    """Lock por número de teléfono usando la tabla `locks` de SQLite (cross-proceso)."""
    def __init__(self, numero):
        self.numero = numero

    def __enter__(self):
        limite = time.time() + LOCK_ESPERA_MAX_SEG
        while True:
            con = _db()
            try:
                ahora = _ahora()
                vencido = _iso(ahora - datetime.timedelta(seconds=LOCK_TTL_SEG))
                # La comparación es de texto, así que solo vale entre fechas del mismo
                # formato. Un lock sin offset lo escribió un proceso anterior al cambio de
                # reloj: ese worker ya no existe (el formato cambió en un restart), así que
                # su lock está huérfano y se descarta igual que uno vencido.
                con.execute("DELETE FROM locks WHERE numero=? AND (adquirido_en<? OR adquirido_en NOT LIKE '%+00:00')",
                            (self.numero, vencido))
                try:
                    con.execute("INSERT INTO locks (numero, adquirido_en) VALUES (?,?)",
                                (self.numero, _iso(ahora)))
                    con.commit()
                    return self
                except sqlite3.IntegrityError:
                    con.rollback()
            finally:
                con.close()
            if time.time() >= limite:
                print("Lock de " + str(self.numero) + " no se liberó a tiempo, sigo de todos modos", flush=True)
                return self
            time.sleep(0.3)

    def __exit__(self, *exc):
        con = _db()
        try:
            con.execute("DELETE FROM locks WHERE numero=?", (self.numero,))
            con.commit()
        finally:
            con.close()


def lock(numero):
    return _LockNumero(numero)


# ---------- estado de la intervención ----------
def _fila(con, numero):
    return con.execute("""SELECT numero, operador, inicio, expira, cerrada_en, motivo_cierre,
                          traspaso_avisado FROM intervenciones WHERE numero=?""", (numero,)).fetchone()


def _cerrar(con, numero, cuando, motivo):
    con.execute("UPDATE intervenciones SET cerrada_en=?, motivo_cierre=? WHERE numero=? AND cerrada_en IS NULL",
                (_iso(cuando), motivo, numero))
    con.commit()


def estado(numero):
    """Intervención vigente de este número, o None. De paso cierra la que ya venció."""
    con = _db()
    try:
        f = _fila(con, numero)
        if not f or f[4]:                       # no hay, o ya está cerrada
            return None
        expira = _parse(f[3])
        ahora = _ahora()
        if not expira or expira <= ahora:       # se acabaron los 30 min -> vuelve el agente
            _cerrar(con, numero, expira or ahora, "tiempo")
            return None
        return {"numero": f[0], "operador": f[1], "inicio": f[2], "expira": f[3],
                "restante_seg": int((expira - ahora).total_seconds())}
    finally:
        con.close()


def activa(numero):
    return estado(numero) is not None


def activas():
    """Números con control humano vigente ahora mismo (para marcarlos en la tabla)."""
    con = _db()
    try:
        filas = con.execute("SELECT numero FROM intervenciones WHERE cerrada_en IS NULL").fetchall()
    finally:
        con.close()
    return [n for (n,) in filas if activa(n)]


def tomar(numero, operador, minutos=None):
    """El operador toma (o renueva) el control. Devuelve el estado resultante."""
    minutos = max(1, min(MINUTOS_MAX, int(minutos or MINUTOS_DEFAULT)))
    ahora = _ahora()
    expira = ahora + datetime.timedelta(minutes=minutos)
    con = _db()
    try:
        f = _fila(con, numero)
        # si ya hay una vigente, se conserva la hora de inicio (solo se estira el plazo)
        vigente = f and not f[4] and (_parse(f[3]) or ahora) > ahora
        inicio = f[2] if vigente else _iso(ahora)
        con.execute("""INSERT INTO intervenciones (numero,operador,inicio,expira,cerrada_en,motivo_cierre,traspaso_avisado)
                       VALUES (?,?,?,?,NULL,NULL,0)
                       ON CONFLICT(numero) DO UPDATE SET
                         operador=excluded.operador, inicio=excluded.inicio, expira=excluded.expira,
                         cerrada_en=NULL, motivo_cierre=NULL, traspaso_avisado=0""",
                    (numero, operador, inicio, _iso(expira)))
        con.commit()
    finally:
        con.close()
    return estado(numero)


def soltar(numero, motivo="manual"):
    """Devuelve el control al agente antes de que se cumpla el plazo."""
    con = _db()
    try:
        _cerrar(con, numero, _ahora(), motivo)
    finally:
        con.close()
    return True


def consumir_traspaso(numero):
    """Si un operador acaba de soltar este chat (por tiempo o a mano) y el agente todavía
    no se ha enterado, devuelve los datos de esa intervención UNA sola vez. Sirve para
    avisarle al agente que retoma una plática que estuvo en manos de una persona."""
    estado(numero)  # cierra la vencida si aplica
    con = _db()
    try:
        f = _fila(con, numero)
        if not f or not f[4] or f[6]:
            return None
        con.execute("UPDATE intervenciones SET traspaso_avisado=1 WHERE numero=?", (numero,))
        con.commit()
        return {"operador": f[1], "inicio": f[2], "fin": f[4], "motivo": f[5] or "tiempo"}
    finally:
        con.close()


def nota_traspaso(tras):
    """Texto que se le inyecta al agente en su primer mensaje después de la intervención."""
    if not tras:
        return None
    razon = ("se le acabó el tiempo de control"
             if tras.get("motivo") == "tiempo" else "devolvió el chat a propósito")
    return ("AVISO DE TRASPASO: los mensajes recientes marcados como tuyos en realidad los escribió "
            "un operador humano del equipo (" + str(tras.get("operador", "")) + ") que tomó el control "
            "de esta plática y ya " + razon + ". Retomas tú. Dale continuidad natural a lo que quedó "
            "dicho: no te presentes de nuevo, no repitas preguntas ya contestadas, no contradigas lo "
            "que el operador prometió o aclaró, y no menciones que hubo un cambio de operador ni que "
            "eres un sistema.")


# ---------- historial que lee el agente ----------
def historial_append(numero, role, content, tope=12):
    """Agrega un turno al mismo historial que usa el agente, para que al retomar tenga
    todo el contexto de lo que se dijo mientras mandaba el operador."""
    con = _db()
    try:
        row = con.execute("SELECT datos FROM historial WHERE numero=?", (numero,)).fetchone()
        try:
            hist = json.loads(row[0]) if row else []
        except (TypeError, ValueError):
            hist = []
        hist.append({"role": role, "content": content})
        con.execute(
            "INSERT INTO historial (numero, datos, actualizado_en) VALUES (?,?,?) "
            "ON CONFLICT(numero) DO UPDATE SET datos=excluded.datos, actualizado_en=excluded.actualizado_en",
            (numero, json.dumps(hist[-tope:], ensure_ascii=False), _iso(_ahora())))
        con.commit()
    finally:
        con.close()


# ---------- bitácora (la misma que lee Explorar) ----------
def registrar(numero, mensaje="", respuesta="", origen="operador", operador=None, extra=None):
    """Escribe en data/registros.jsonl con el mismo formato de siempre, para que el
    mensaje aparezca en Explorar y en el CSV sin tratarlo distinto."""
    reg = {"fecha": _iso(_ahora()), "origen": origen, "numero": numero,
           "mensaje": mensaje, "respuesta": respuesta}
    if operador:
        reg["operador"] = operador
    if extra:
        reg.update(extra)
    os.makedirs(os.path.dirname(REGISTROS), exist_ok=True)
    with open(REGISTROS, "a", encoding="utf-8") as f:
        f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    return reg


# ---------- envío manual al ciudadano ----------
def _enviar_green(numero, texto):
    if not green_api.configurado():
        return False, "Green API no está configurada"
    chat_id = numero if "@" in numero else numero + "@c.us"
    return (True, "green") if green_api.enviar(chat_id, texto) else (False, "Green API rechazó el envío")


def _enviar_meta(numero, texto):
    token = os.environ.get("WHATSAPP_TOKEN", "")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not (token and phone_id):
        return False, "WhatsApp Cloud API sin credenciales"
    import requests
    try:
        r = requests.post("https://graph.facebook.com/v21.0/" + phone_id + "/messages",
                          headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"},
                          json={"messaging_product": "whatsapp", "to": numero.split("@")[0],
                                "type": "text", "text": {"body": texto}}, timeout=20)
        return (True, "whatsapp") if r.ok else (False, "Meta respondió " + str(r.status_code))
    except Exception as e:
        return False, str(e)


def enviar(numero, texto, canal=None):
    """Manda el mensaje del operador al ciudadano por el canal que usa ese contacto.
    Devuelve (ok, detalle)."""
    texto = (texto or "").strip()
    if not texto:
        return False, "mensaje vacío"
    canal = (canal or "").lower()
    orden = [_enviar_green, _enviar_meta]
    if canal.startswith("whatsapp"):          # contacto que llegó por la API oficial de Meta
        orden = [_enviar_meta, _enviar_green]
    ultimo = "no hay ningún canal de WhatsApp configurado"
    for fn in orden:
        ok, detalle = fn(numero, texto)
        if ok:
            return True, detalle
        ultimo = detalle
    return False, ultimo
