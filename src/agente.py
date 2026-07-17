#!/usr/bin/env python3
"""
Agente de Froy — núcleo con LOOP de refinamiento para que NO parezca bot.

Flujo por cada mensaje del ciudadano:
  1) BORRADOR  -> el modelo redacta respuesta + metadatos (JSON).
  2) CRÍTICA   -> otro paso revisa naturalidad (¿suena humano?) y cumplimiento legal (AAPC).
  3) REFINA    -> si la crítica encuentra problemas, reescribe. Se repite hasta MAX_LOOPS.

Requiere ANTHROPIC_API_KEY (ver .env.example).
"""
import os, re, glob, json, sys, datetime

try:
    import anthropic
except ImportError:
    sys.exit("Falta el SDK: pip install anthropic")

# ---------- Config ----------
MODELO_BORRADOR = os.environ.get("MODELO_BORRADOR", "claude-sonnet-4-6")
MODELO_CRITICO  = os.environ.get("MODELO_CRITICO",  "claude-sonnet-4-6")
MAX_LOOPS = int(os.environ.get("MAX_LOOPS", "2"))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _cargar_env():
    env = os.path.join(BASE, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #", 1)[0]
                os.environ.setdefault(k.strip(), v.strip())
_cargar_env()

def cargar_kb():
    """Solo la base de conocimiento (los .md), sin el system prompt."""
    partes = ["# BASE DE CONOCIMIENTO\n"]
    for f in sorted(glob.glob(os.path.join(BASE, "knowledge-base/*.md"))):
        partes.append(f"\n\n--- {os.path.basename(f)} ---\n" + open(f, encoding="utf-8").read())
    return "".join(partes)

def cargar_conocimiento():
    """System prompt + toda la base de conocimiento como contexto del modelo."""
    return open(os.path.join(BASE, "src/prompt/system-prompt.md"), encoding="utf-8").read() + "\n\n" + cargar_kb()

KB_TEXT = cargar_kb()
SYSTEM = cargar_conocimiento()
cliente = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY

def recargar_conocimiento():
    """Vuelve a leer la base de conocimiento (tras integrar material nuevo)."""
    global SYSTEM, KB_TEXT
    KB_TEXT = cargar_kb()
    SYSTEM = cargar_conocimiento()
    return len(SYSTEM)

def _texto(resp):
    return "".join(b.text for b in resp.content if b.type == "text").strip()

def _json_de(texto):
    """Extrae el primer objeto JSON del texto (el modelo a veces lo envuelve)."""
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return {"respuesta": texto, "meta": {}}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"respuesta": texto, "meta": {}}

def _contexto_contacto(contacto):
    """Texto corto con lo ya sabido de este número, para no volver a preguntarlo."""
    if not contacto:
        return None
    partes = []
    if contacto.get("nombre"):
        partes.append(f"se llama {contacto['nombre']}")
    if contacto.get("ciudad"):
        partes.append(f"es de {contacto['ciudad']}")
    if contacto.get("colonia"):
        partes.append(f"colonia {contacto['colonia']}")
    if not partes:
        return None
    return ("Dato ya conocido de este contacto (no se lo vuelvas a preguntar, "
            "solo repítelo en meta si la persona lo reafirma): " + ", ".join(partes) + ".")

def _system_con_contacto(contacto):
    ctx = _contexto_contacto(contacto)
    if not ctx:
        return SYSTEM
    return [{"type": "text", "text": SYSTEM}, {"type": "text", "text": ctx}]

# ---------- 1) Borrador ----------
def _borrador(historial, contacto=None):
    resp = cliente.messages.create(
        model=MODELO_BORRADOR, max_tokens=700, system=_system_con_contacto(contacto),
        messages=historial)
    return _json_de(_texto(resp))

# ---------- 2) Crítica ----------
CRITICO_SYS = """Eres editor del equipo de Froy. Revisas la respuesta ANTES de enviarse al ciudadano. Eres estricto pero JUSTO.

REGLA CLAVE: al final de estas instrucciones tienes la BASE DE CONOCIMIENTO oficial. TODO lo que aparezca
ahí (cifras, montos, programas de becas, hospitales, el portal becasycredito.sonora.gob.mx, el 40% de
escuela privada, los teléfonos, etc.) está VERIFICADO y el bot SÍ puede decirlo — NO lo marques como
inventado. Solo marca como inventado lo que NO esté respaldado por la base de conocimiento o la contradiga.

REGLA CLAVE 2 — MEMORIA DEL CONTACTO: si estas instrucciones incluyen un bloque "Dato ya conocido de
este contacto" (nombre, ciudad, colonia), el bot tiene memoria persistente por número de WhatsApp —
usar esos datos (p. ej. saludar por su nombre) es memoria legítima del sistema, NO es un dato inventado,
aunque el ciudadano no lo haya dicho en ESTE mensaje puntual. Tampoco lo marques como inventado si el
dato (nombre, ciudad, colonia) aparece textualmente en el MENSAJE DEL CIUDADANO de abajo. Solo marca como
inventado un nombre/dato personal si NO viene ni del contacto ya conocido ni del mensaje actual.

A) CUMPLIMIENTO LEGAL (acto anticipado de campaña). RECHAZA si la respuesta:
   - pide o confirma el voto ("vota por", "vótame", "sí, dame tu voto")
   - menciona boleta o fecha del proceso ligadas a Froy
   - adelanta candidatura a gobernador o cualquier elección de gobierno
   - hace PROMESAS de gestión personal futura (que Froy resolverá algo, intervendrá, "si te ponen trabas
     yo lo arreglo"). OJO: **"Cuentan conmigo" y "ya sabes que cuentas conmigo" son su FRASE ICÓNICA,
     NO son promesa — NO las rechaces por eso.**
   - inventa cifras/montos/datos que NO estén en la base de conocimiento de abajo
   - INVENTA DATOS PERSONALES de Froy no documentados: edad exacta, año/fecha de nacimiento, signo
     zodiacal. Si la respuesta da un número de edad o una fecha de nacimiento, RECHÁZALA.
   - nombra a otros aspirantes o políticos en negativo
   - DICE EL NOMBRE DEL GOBERNADOR de Sonora ("Alfonso Durazo", "Durazo" o apellidos). Prohibido nombrarlo.
     "El Gobernador" como título genérico sí se permite; su NOMBRE no.
   - USA LA PALABRA "boleta" (aunque sea para negarla). Debe decir "no es una elección de gobierno".
   - INVENTA PASOS DE TRÁMITE O FUENTES que NO estén en la base de conocimiento (ej. inventar "Servicio
     Nacional de Empleo", "Jóvenes Construyendo el Futuro", pensiones, módulos o requisitos que no están
     documentados). Los datos, portales y links que SÍ están en la base de conocimiento (como el portal
     de becas) SÍ se pueden compartir sin problema.

B) NATURALIDAD (que NO parezca bot). RECHAZA si la respuesta:
   - suena a folleto o a comunicado ("Estimado ciudadano", "le informamos")
   - es LARGA: más de 3 párrafos cortos o más de ~500 caracteres. En WhatsApp se escribe corto.
   - usa listas con viñetas, guiones, numeración, encabezados o negritas. Prohibido. (Excepción
     permitida: el menú corto con 🔹 del flujo "¿Quién es Froy?" — ese NO lo rechaces.)
   - mete más de UN dato fuerte por respuesta (no hay que soltar todo de un jalón)
   - es genérica, fría o repite una plantilla
   - no reconoce lo que dijo la persona ni invita a seguir la plática
   - CONTIENE GROSERÍAS (chingo, chingón, cabrón, pinche, verga, pendejo). Cero groserías. (Nota:
     "órale" no es grosería pero está vetada por estilo; "un chorro/un montón" SÍ se permiten.)

Devuelve SOLO este JSON:
{"ok": true|false, "problemas": ["..."], "sugerencia": "qué cambiar en concreto"}"""

def _critica(mensaje, borrador, contacto=None):
    sysblocks = [{"type": "text", "text": CRITICO_SYS}]
    ctx = _contexto_contacto(contacto)
    if ctx:
        sysblocks.append({"type": "text", "text": ctx})
    sysblocks.append({"type": "text",
                       "text": "\n\n===== BASE DE CONOCIMIENTO (todo esto es VERIFICADO) =====\n" + KB_TEXT,
                       "cache_control": {"type": "ephemeral"}})
    resp = cliente.messages.create(
        model=MODELO_CRITICO, max_tokens=400, system=sysblocks,
        messages=[{"role": "user", "content":
            f"MENSAJE DEL CIUDADANO:\n{mensaje}\n\nRESPUESTA PROPUESTA:\n{borrador.get('respuesta','')}"}])
    return _json_de(_texto(resp))

# ---------- 3) Refinar ----------
def _refinar(historial, borrador, critica, contacto=None):
    instr = (f"Tu respuesta anterior fue:\n{borrador.get('respuesta','')}\n\n"
             f"El editor la rechazó por: {', '.join(critica.get('problemas', []))}\n"
             f"Indicación: {critica.get('sugerencia','')}\n\n"
             "Reescríbela corrigiendo eso. Devuelve el mismo formato JSON {respuesta, meta}.")
    resp = cliente.messages.create(
        model=MODELO_BORRADOR, max_tokens=700, system=_system_con_contacto(contacto),
        messages=historial + [{"role": "user", "content": instr}])
    return _json_de(_texto(resp))

# Red de seguridad determinista: groserías que nunca deben pasar.
PROFANIDAD = re.compile(r"\b(chingo|chingón|chingona|cabrón|cabrones|pinche|verga|pendej|"
                        r"culer|mierda|joder|carajo|put[ao])\w*", re.IGNORECASE)

def _checar_duro(texto):
    """Devuelve lista de problemas detectados de forma determinista (no depende del modelo)."""
    problemas = []
    if PROFANIDAD.search(texto):
        problemas.append("Contiene una grosería; reescribe coloquial pero limpio, sin groserías.")
    if "durazo" in texto.lower():
        problemas.append("Nombra al gobernador; quita el nombre, refiérete solo a 'el Gobierno del Estado'.")
    if re.search(r"\bboleta\b", texto, re.IGNORECASE):
        problemas.append("Usa la palabra 'boleta'; di 'no es una elección de gobierno' sin nombrarla.")
    if re.search(r"\bór?ale\b", texto, re.IGNORECASE):
        problemas.append("Usa 'órale' (está vetado por estilo); cámbialo por 'Qué onda' u otra apertura.")
    return problemas

# Auditoría de cumplimiento: registra cuándo se bloqueó un borrador por razones legales.
AUDIT = os.path.join(BASE, "data", "auditoria.jsonl")
COMPLIANCE_RE = re.compile(r"voto|vota|vótame|gobernador|durazo|boleta|promes|promet|"
                           r"inventa|cifra|gubernatura|candidat|recursos públicos", re.I)

def _auditar(mensaje, borrador_malo, motivos):
    try:
        os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
        with open(AUDIT, "a") as f:
            f.write(json.dumps({
                "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
                "mensaje": mensaje, "bloqueado": borrador_malo, "motivos": motivos,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ---------- Orquestación ----------
def responder(mensaje, historial=None, verbose=False, contacto=None):
    historial = (historial or []) + [{"role": "user", "content": mensaje}]
    salida = _borrador(historial, contacto)
    for i in range(MAX_LOOPS):
        crit = _critica(mensaje, salida, contacto)
        duros = _checar_duro(salida.get("respuesta", ""))
        # auditar si hubo un problema de cumplimiento (determinista o del crítico)
        motivos = list(duros)
        if not crit.get("ok"):
            motivos += [p for p in crit.get("problemas", []) if COMPLIANCE_RE.search(p)]
        if motivos:
            _auditar(mensaje, salida.get("respuesta", ""), motivos)
        if duros:  # los filtros deterministas mandan: forzar corrección
            crit = {"ok": False, "problemas": duros, "sugerencia": " ".join(duros)}
        if verbose:
            print(f"  [loop {i+1}] ok={crit.get('ok')} {crit.get('problemas','')}")
        if crit.get("ok"):
            break
        salida = _refinar(historial, salida, crit, contacto)
    return salida

if __name__ == "__main__":
    print("Agente de Froy (escribe 'salir' para terminar)\n")
    hist = []
    while True:
        try:
            msg = input("Ciudadano: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in ("salir", "exit", "quit"):
            break
        out = responder(msg, hist, verbose=True)
        print(f"Froy-bot: {out.get('respuesta','')}")
        print(f"  meta: {json.dumps(out.get('meta',{}), ensure_ascii=False)}\n")
        hist += [{"role": "user", "content": msg},
                 {"role": "assistant", "content": out.get("respuesta", "")}]
