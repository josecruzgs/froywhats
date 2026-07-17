#!/usr/bin/env python3
"""
Dashboard de administración del agente de Froy.

Una sola app con 4 áreas:
  📊 Resumen    — estadísticas y segmentación geográfica (de data/registros.jsonl)
  🧪 Pruebas    — chatear con el agente y anotar mejoras
  📚 Alimentar  — subir info y APROBAR/RECHAZAR aportes (la aprobación integra a la base)
  💡 Notas      — mejoras anotadas por el equipo

Correr:  python src/dashboard.py   ->  http://localhost:8080
"""
import os, sys, json, re, csv, io, datetime, sqlite3
from collections import Counter
from flask import Flask, request, jsonify, Response, g, abort

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agente, humanizar, green_api

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(BASE, "knowledge-base")
DATA = os.path.join(BASE, "data")
APORTES = os.path.join(DATA, "aportes")
APROBADOS = os.path.join(DATA, "aportes_aprobados")
RECHAZADOS = os.path.join(DATA, "aportes_rechazados")
NOTAS = os.path.join(DATA, "notas_mejora.jsonl")
REGISTROS = os.path.join(DATA, "registros.jsonl")
USUARIOS_F = os.path.join(DATA, "usuarios.json")
ESCAL_F = os.path.join(DATA, "escalados_atendidos.json")
AUDITORIA_F = os.path.join(DATA, "auditoria.jsonl")
ACCIONES_F = os.path.join(DATA, "acciones_digitales.json")
META_F = os.path.join(DATA, "meta_seguimiento.json")
EVID_DIR = os.path.join(DATA, "evidencias")
CONV_DB = os.path.join(DATA, "conversaciones.db")
for d in (APORTES, APROBADOS, RECHAZADOS, EVID_DIR):
    os.makedirs(d, exist_ok=True)

def listar_contactos():
    """Perfiles de contacto (nombre/ciudad/colonia/postura) que arma server.py por cada
    número que escribe. Tabla vive en el mismo conversaciones.db, se lee de solo lectura aquí."""
    if not os.path.exists(CONV_DB):
        return []
    con = sqlite3.connect(CONV_DB, timeout=10)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS contactos (
            numero TEXT PRIMARY KEY, nombre TEXT, ciudad TEXT, colonia TEXT, tema TEXT,
            postura TEXT, canal TEXT, mensajes INTEGER NOT NULL DEFAULT 0,
            primera_vez TEXT NOT NULL, actualizado_en TEXT NOT NULL)""")
        filas = con.execute("""SELECT numero,nombre,ciudad,colonia,tema,postura,canal,
            mensajes,primera_vez,actualizado_en FROM contactos ORDER BY actualizado_en DESC""").fetchall()
    finally:
        con.close()
    cols = ["numero", "nombre", "ciudad", "colonia", "tema", "postura", "canal",
            "mensajes", "primera_vez", "actualizado_en"]
    return [dict(zip(cols, f)) for f in filas]

app = Flask(__name__)

# ---------- seguridad: usuarios y roles ----------
PANEL_USER = os.environ.get("PANEL_USER", "admin")
PANEL_PASS = os.environ.get("PANEL_PASS", "")  # vacío = sin protección (solo para local)
ROLES = ("admin", "coordinador", "brigadista")

def cargar_usuarios():
    if os.path.exists(USUARIOS_F):
        try: return json.load(open(USUARIOS_F))
        except: return []
    return []

def guardar_usuarios(us):
    json.dump(us, open(USUARIOS_F, "w"), ensure_ascii=False, indent=2)

def _validar(usuario, clave):
    """Devuelve el rol si las credenciales son válidas, o None."""
    if PANEL_PASS and usuario == PANEL_USER and clave == PANEL_PASS:
        return "admin"  # superusuario del .env
    for u in cargar_usuarios():
        if u.get("usuario") == usuario and u.get("clave") == clave:
            return u.get("rol", "brigadista")
    return None

@app.before_request
def _proteger():
    if not PANEL_PASS:                      # sin contraseña configurada (desarrollo local)
        g.usuario, g.rol = "local", "admin"
        return
    a = request.authorization
    rol = _validar(a.username, a.password) if a else None
    if not rol:
        return Response("Acceso restringido", 401,
                        {"WWW-Authenticate": 'Basic realm="Panel de Froy"'})
    g.usuario, g.rol = a.username, rol

def _solo_admin():
    if getattr(g, "rol", None) != "admin":
        abort(403)

@app.get("/api/me")
def api_me():
    return jsonify({"usuario": getattr(g, "usuario", ""), "rol": getattr(g, "rol", "")})

# ---------- API: conexión Green API (solo admin) ----------
@app.get("/api/green-config")
def api_green_config_get():
    _solo_admin()
    return jsonify(green_api.cargar_config())

@app.post("/api/green-config")
def api_green_config_set():
    _solo_admin()
    d = request.get_json(force=True)
    green_api.guardar_config(d.get("id_instance", ""), d.get("api_token", ""), d.get("api_url", ""))
    return jsonify({"ok": True})

@app.get("/api/green-estado")
def api_green_estado():
    _solo_admin()
    return jsonify(green_api.estado())

# ---------- utilidades ----------
def _slug(t):
    t = re.sub(r"[^\w\s-]", "", (t or "").lower()).strip()
    return re.sub(r"\s+", "-", t)[:40] or "aporte"

def _seguro(nombre):  # evita path traversal
    return os.path.basename(nombre or "")

def cargar_registros():
    regs = []
    if os.path.exists(REGISTROS):
        for l in open(REGISTROS):
            if l.strip():
                try: regs.append(json.loads(l))
                except: pass
    return regs

def cargar_notas():
    notas = []
    if os.path.exists(NOTAS):
        for i, l in enumerate(open(NOTAS)):
            if l.strip():
                try:
                    n = json.loads(l); n["_id"] = i; notas.append(n)
                except: pass
    return notas

# ---------- API: estadísticas ----------
@app.get("/api/stats")
def api_stats():
    regs = cargar_registros()
    total = len(regs)
    tipos = Counter(r.get("tipo") or "otro" for r in regs)
    munis = Counter(r.get("municipio") for r in regs if r.get("municipio"))
    cols = Counter(r.get("colonia") for r in regs if r.get("colonia"))
    escal = sum(1 for r in regs if r.get("escalar"))
    notas = cargar_notas()
    pend = [a for a in os.listdir(APORTES) if a.endswith(".md")]
    return jsonify({
        "total": total,
        "escalados": escal,
        "pct_escalados": round(100 * escal / total) if total else 0,
        "municipios_alcanzados": len(munis),
        "por_tipo": tipos.most_common(),
        "top_municipios": munis.most_common(8),
        "top_colonias": cols.most_common(8),
        "notas": len(notas),
        "aportes_pendientes": len(pend),
        "recientes": [
            {"mensaje": r.get("mensaje", ""), "tipo": r.get("tipo"),
             "municipio": r.get("municipio"), "colonia": r.get("colonia"),
             "escalar": r.get("escalar"), "fecha": r.get("fecha", "")[:16].replace("T", " ")}
            for r in regs[-8:][::-1]
        ],
    })

# ---------- API: temas frecuentes y tendencias ----------
TOPICOS = {
    "Agua": ["agua", "sequía", "sequia", "acueducto", "oomapas", "pipa"],
    "Becas": ["beca", "becas"],
    "Educación": ["educación", "educacion", "maestro", "magisterio", "escuela", "clases", "sec "],
    "Seguridad": ["inseguridad", "asalto", "robo", "violencia", "seguridad", "narco", "balac"],
    "Salud": ["salud", "hospital", "medicina", "clínica", "clinica", "imss", "doctor", "enferm"],
    "Pensión": ["pensión", "pension", "adulto mayor", "adultos mayores", "jubil"],
    "Empleo": ["empleo", "trabajo", "desempleo", "chamba"],
    "Vivienda": ["vivienda", "renta", "infonavit", "fovissste"],
    "Apoyos/Crédito": ["apoyo", "programa", "crédito", "credito", "pyme", "negocio"],
    "Calles/Baches": ["bache", "baches", "pavimento", "banqueta"],
    "Luz/CFE": ["luz", "cfe", "apagón", "apagon", "energía", "energia"],
    "Sobre Froylán": ["froy", "froylán", "froylan", "quién es", "quien es"],
}

@app.get("/api/tendencias")
def api_tendencias():
    regs = cargar_registros()
    conteo = {}
    for r in regs:
        txt = ((r.get("mensaje", "") or "") + " " + (r.get("tema", "") or "")).lower()
        for tema, kws in TOPICOS.items():
            if any(k in txt for k in kws):
                conteo[tema] = conteo.get(tema, 0) + 1
    temas = sorted(conteo.items(), key=lambda x: -x[1])
    # serie de los últimos 14 días (incluye días en cero)
    hoy = datetime.date.today()
    dias = [hoy - datetime.timedelta(days=i) for i in range(13, -1, -1)]
    porfecha = {}
    for r in regs:
        f = (r.get("fecha") or "")[:10]
        porfecha[f] = porfecha.get(f, 0) + 1
    serie = [{"dia": d.strftime("%d/%m"), "total": porfecha.get(d.isoformat(), 0)} for d in dias]
    return jsonify({"temas": temas, "serie": serie, "total": len(regs)})

# ---------- API: mapa georreferenciado ----------
def _norm(s):
    s = (s or "").strip().lower()
    for a, b in zip("áéíóúü", "aeiouu"):
        s = s.replace(a, b)
    return s

COORDS = {
    "hermosillo": (29.0729, -110.9559), "cajeme": (27.4863, -109.9303),
    "ciudad obregon": (27.4863, -109.9303), "obregon": (27.4863, -109.9303),
    "nogales": (31.3186, -110.9458), "san luis rio colorado": (32.4595, -114.7722),
    "slrc": (32.4595, -114.7722), "navojoa": (27.0717, -109.4437),
    "guaymas": (27.9183, -110.8989), "agua prieta": (31.3253, -109.5487),
    "caborca": (30.7158, -112.1538), "puerto penasco": (31.3167, -113.5333),
    "penasco": (31.3167, -113.5333), "empalme": (27.9619, -110.8126),
    "huatabampo": (26.8267, -109.6411), "magdalena": (30.6316, -110.9569),
    "cananea": (30.9876, -110.3007), "etchojoa": (26.9000, -109.6300),
    "alamos": (27.0256, -108.9389), "sonoyta": (31.8600, -112.8500),
    "nacozari": (30.3900, -109.6800),
}

@app.get("/api/mapa")
def api_mapa():
    c = Counter(r.get("municipio") for r in cargar_registros() if r.get("municipio"))
    puntos, sin = [], []
    for m, n in c.items():
        co = COORDS.get(_norm(m))
        if co:
            puntos.append({"municipio": m, "lat": co[0], "lng": co[1], "total": n})
        else:
            sin.append({"municipio": m, "total": n})
    return jsonify({"puntos": puntos, "sin_coords": sorted(sin, key=lambda x: -x["total"])})

# ---------- API: bandeja de escalados ----------
def _cargar_set(path):
    if os.path.exists(path):
        try: return set(json.load(open(path)))
        except: return set()
    return set()

@app.get("/api/escalados")
def api_escalados():
    """Agrupa los escalados POR CONVERSACIÓN (número), para no repetir el mismo chat."""
    atend = _cargar_set(ESCAL_F)
    grupos = {}
    for r in cargar_registros():
        if not r.get("escalar"):
            continue
        num = r.get("numero") or "?"
        gp = grupos.setdefault(num, {"numero": num, "mensajes": [], "fecha": "",
                                     "municipio": None, "colonia": None, "temas": []})
        gp["mensajes"].append(r.get("mensaje", ""))
        gp["fecha"] = (r.get("fecha") or "")[:16].replace("T", " ")
        gp["municipio"] = r.get("municipio") or gp["municipio"]
        gp["colonia"] = r.get("colonia") or gp["colonia"]
        for t in (r.get("tema"), r.get("motivo_escalamiento")):
            if t and t not in gp["temas"]:
                gp["temas"].append(t)
    items = [{"numero": g["numero"], "fecha": g["fecha"], "mensajes": g["mensajes"],
              "n": len(g["mensajes"]), "municipio": g["municipio"], "colonia": g["colonia"],
              "tema": ", ".join(g["temas"][:3]), "atendido": g["numero"] in atend}
             for g in grupos.values()]
    items.sort(key=lambda x: (x["atendido"], x["fecha"]), reverse=False)
    return jsonify(items)

@app.post("/api/escalados/atender")
def api_escalados_atender():
    num = request.get_json(force=True).get("numero")
    atend = _cargar_set(ESCAL_F)
    atend.discard(num) if num in atend else atend.add(num)
    json.dump(sorted(atend), open(ESCAL_F, "w"))
    return jsonify({"ok": True})

# ---------- API: explorar y exportar ----------
@app.get("/api/conversaciones")
def api_conversaciones():
    q = (request.args.get("q") or "").lower().strip()
    tipo = request.args.get("tipo") or ""
    out = []
    for i, r in enumerate(cargar_registros()):
        if tipo and (r.get("tipo") or "") != tipo:
            continue
        blob = " ".join(str(r.get(k, "") or "") for k in
                        ("mensaje", "respuesta", "colonia", "municipio")).lower()
        if q and q not in blob:
            continue
        out.append({"id": i, "fecha": (r.get("fecha") or "")[:16].replace("T", " "),
                    "mensaje": r.get("mensaje", ""), "respuesta": r.get("respuesta", ""),
                    "tipo": r.get("tipo"), "municipio": r.get("municipio"),
                    "colonia": r.get("colonia"), "escalar": r.get("escalar"),
                    "origen": r.get("origen")})
    return jsonify({"total": len(out), "items": out[-200:][::-1]})

@app.get("/api/contactos")
def api_contactos():
    q = (request.args.get("q") or "").lower().strip()
    postura = request.args.get("postura") or ""
    out = []
    for c in listar_contactos():
        if postura and (c.get("postura") or "sin_datos") != postura:
            continue
        blob = " ".join(str(c.get(k, "") or "") for k in
                        ("numero", "nombre", "ciudad", "colonia", "tema")).lower()
        if q and q not in blob:
            continue
        out.append(c)
    return jsonify({"total": len(out), "items": out})

@app.get("/export.csv")
def export_csv():
    buf = io.StringIO()
    cols = ["fecha", "origen", "numero", "tipo", "tema", "municipio", "colonia",
            "audiencia", "escalar", "mensaje", "respuesta"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in cargar_registros():
        w.writerow(r)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=conversaciones_froy.csv"})

# ---------- API: auditoría de cumplimiento ----------
@app.get("/api/auditoria")
def api_auditoria():
    items = []
    if os.path.exists(AUDITORIA_F):
        for l in open(AUDITORIA_F):
            if l.strip():
                try: items.append(json.loads(l))
                except: pass
    return jsonify(items[::-1][:200])

# ---------- API: acciones en redes (war room digital) ----------
def cargar_acciones():
    if os.path.exists(ACCIONES_F):
        try: return json.load(open(ACCIONES_F))
        except: return []
    return []

def guardar_acciones(a):
    json.dump(a, open(ACCIONES_F, "w"), ensure_ascii=False, indent=2)

@app.get("/api/acciones")
def api_acciones():
    return jsonify(cargar_acciones()[::-1])

@app.post("/api/acciones")
def api_acciones_add():
    if getattr(g, "rol", None) not in ("admin", "coordinador"):
        abort(403)
    d = request.get_json(force=True)
    link = (d.get("link") or "").strip()
    if not link:
        return jsonify({"error": "falta el link"}), 400
    acc = cargar_acciones()
    nid = (max([x.get("id", 0) for x in acc]) + 1) if acc else 1
    acc.append({"id": nid, "fecha": datetime.datetime.now().isoformat(timespec="minutes"),
                "autor": getattr(g, "usuario", ""), "red": d.get("red", ""), "link": link,
                "accion": d.get("accion", ""), "comentario": (d.get("comentario") or "").strip(),
                "perfil": d.get("perfil", ""), "estado": "pendiente"})
    guardar_acciones(acc)
    return jsonify({"ok": True})

@app.post("/api/acciones/estado")
def api_acciones_estado():
    i = request.get_json(force=True).get("id")
    acc = cargar_acciones()
    for x in acc:
        if x.get("id") == i:
            x["estado"] = "pendiente" if x.get("estado") == "hecho" else "hecho"
    guardar_acciones(acc)
    return jsonify({"ok": True})

@app.post("/api/acciones/eliminar")
def api_acciones_del():
    if getattr(g, "rol", None) not in ("admin", "coordinador"):
        abort(403)
    i = request.get_json(force=True).get("id")
    guardar_acciones([x for x in cargar_acciones() if x.get("id") != i])
    return jsonify({"ok": True})

@app.post("/api/acciones/editar")
def api_acciones_editar():
    if getattr(g, "rol", None) not in ("admin", "coordinador"):
        abort(403)
    d = request.get_json(force=True)
    i = d.get("id")
    acc = cargar_acciones()
    for x in acc:
        if x.get("id") == i:
            for k in ("red", "link", "accion", "perfil", "comentario"):
                if k in d:
                    x[k] = (d.get(k) or "").strip()
    guardar_acciones(acc)
    return jsonify({"ok": True})

@app.post("/api/acciones/evidencia")
def api_evidencia():
    """Cualquiera del equipo (incl. brigadistas) sube una foto/captura como evidencia."""
    try:
        i = int(request.form.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "id inválido"}), 400
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"error": "falta la imagen"}), 400
    ext = os.path.splitext(archivo.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"):
        return jsonify({"error": "formato no soportado (usa una imagen)"}), 400
    sello = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    nombre = f"{i}-{sello}{ext}"
    archivo.save(os.path.join(EVID_DIR, nombre))
    acc = cargar_acciones()
    for x in acc:
        if x.get("id") == i:
            x.setdefault("evidencias", []).append(
                {"archivo": nombre, "autor": getattr(g, "usuario", ""),
                 "fecha": datetime.datetime.now().isoformat(timespec="minutes")})
            x["estado"] = "hecho"        # subir evidencia marca la acción como hecha
            x["columna"] = "evidencia"   # y la mueve a la columna 'Con evidencia' del kanban
    guardar_acciones(acc)
    return jsonify({"ok": True, "archivo": nombre})

@app.get("/evidencia/<nombre>")
def ver_evidencia(nombre):
    from flask import send_file
    ruta = os.path.join(EVID_DIR, _seguro(nombre))
    if not os.path.exists(ruta):
        abort(404)
    return send_file(ruta)

# ---------- API: seguimiento (tablero Kanban) ----------
COLS = [("porhacer", "Por hacer"), ("proceso", "En proceso"),
        ("hecho", "Hecho"), ("evidencia", "Con evidencia")]
COL_KEYS = {k for k, _ in COLS}

def _norm_accion(x):
    """Asigna la columna del kanban si falta (compatibilidad con acciones viejas)."""
    if not x.get("columna"):
        if x.get("evidencias"): x["columna"] = "evidencia"
        elif x.get("estado") == "hecho": x["columna"] = "hecho"
        else: x["columna"] = "porhacer"
    return x

@app.post("/api/acciones/columna")
def api_acciones_columna():
    d = request.get_json(force=True)
    i, col = d.get("id"), d.get("columna")
    if col not in COL_KEYS:
        return jsonify({"error": "columna inválida"}), 400
    acc = cargar_acciones()
    for x in acc:
        if x.get("id") == i:
            x["columna"] = col
            x["estado"] = "hecho" if col in ("hecho", "evidencia") else (
                "en proceso" if col == "proceso" else "pendiente")
    guardar_acciones(acc)
    return jsonify({"ok": True})

def cargar_meta():
    if os.path.exists(META_F):
        try: return json.load(open(META_F))
        except: pass
    return {"texto": "", "objetivo": 0}

@app.get("/api/meta")
def api_meta_get():
    return jsonify(cargar_meta())

@app.post("/api/meta")
def api_meta_set():
    if getattr(g, "rol", None) not in ("admin", "coordinador"):
        abort(403)
    d = request.get_json(force=True)
    m = {"texto": (d.get("texto") or "").strip(), "objetivo": int(d.get("objetivo") or 0)}
    json.dump(m, open(META_F, "w"), ensure_ascii=False)
    return jsonify({"ok": True})

@app.get("/api/seguimiento")
def api_seguimiento():
    acc = [_norm_accion(x) for x in cargar_acciones()]
    columnas = [{"key": k, "titulo": t, "items": [x for x in acc if x.get("columna") == k]}
                for k, t in COLS]
    persona = Counter()
    for x in acc:
        for e in x.get("evidencias", []):
            persona[e.get("autor") or "?"] += 1
    meta = cargar_meta()
    meta["avance"] = sum(1 for x in acc if x.get("evidencias"))
    return jsonify({"columnas": columnas, "por_persona": persona.most_common(8),
                    "meta": meta, "total": len(acc)})

# ---------- API: gestión de usuarios (solo admin) ----------
@app.get("/api/usuarios")
def api_usuarios():
    _solo_admin()
    return jsonify([{"usuario": u["usuario"], "rol": u.get("rol")} for u in cargar_usuarios()])

@app.post("/api/usuarios")
def api_usuarios_add():
    _solo_admin()
    d = request.get_json(force=True)
    u = (d.get("usuario") or "").strip(); c = (d.get("clave") or "").strip(); rol = d.get("rol")
    if not u or not c or rol not in ROLES:
        return jsonify({"error": "datos inválidos"}), 400
    us = [x for x in cargar_usuarios() if x.get("usuario") != u]
    us.append({"usuario": u, "clave": c, "rol": rol})
    guardar_usuarios(us)
    return jsonify({"ok": True})

@app.post("/api/usuarios/eliminar")
def api_usuarios_del():
    _solo_admin()
    u = request.get_json(force=True).get("usuario")
    guardar_usuarios([x for x in cargar_usuarios() if x.get("usuario") != u])
    return jsonify({"ok": True})

# ---------- API: chat de pruebas ----------
@app.post("/api/chat")
def api_chat():
    d = request.get_json(force=True)
    mensaje = (d.get("mensaje") or "").strip()
    if not mensaje:
        return jsonify({"error": "mensaje vacío"}), 400
    salida = agente.responder(mensaje, d.get("historial") or [])
    respuesta = salida.get("respuesta", "")
    meta = salida.get("meta", {})
    # registrar la prueba para que alimente las estadísticas
    with open(REGISTROS, "a") as f:
        f.write(json.dumps({
            "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
            "origen": "prueba", "numero": "prueba", "mensaje": mensaje,
            "respuesta": respuesta,
            **{k: meta.get(k) for k in ("tipo", "tema", "municipio", "colonia",
                                        "audiencia", "escalar", "motivo_escalamiento")},
        }, ensure_ascii=False) + "\n")
    return jsonify({"respuesta": respuesta,
                    "globos": humanizar.dividir_en_globos(respuesta), "meta": meta})

# ---------- API: notas de mejora ----------
ESTADOS = ("pendiente", "en progreso", "resuelta")

@app.post("/api/nota")
def api_nota():
    d = request.get_json(force=True)
    nota = (d.get("nota") or "").strip()
    if not nota:
        return jsonify({"error": "nota vacía"}), 400
    with open(NOTAS, "a") as f:
        f.write(json.dumps({
            "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
            "autor": (d.get("autor") or "anónimo").strip(),
            "mensaje": d.get("mensaje", ""), "respuesta": d.get("respuesta", ""),
            "nota": nota,
            "categoria": (d.get("categoria") or "otro").strip(),
            "prioridad": (d.get("prioridad") or "media").strip(),
            "estado": "pendiente",
            "respuesta_ideal": (d.get("respuesta_ideal") or "").strip(),
        }, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})

@app.get("/api/notas")
def api_notas():
    notas = cargar_notas()
    for n in notas:  # compatibilidad con notas viejas (campo 'resuelta')
        n.setdefault("estado", "resuelta" if n.get("resuelta") else "pendiente")
        n.setdefault("categoria", "otro"); n.setdefault("prioridad", "media")
        n.setdefault("respuesta_ideal", "")
    return jsonify(notas[::-1])

@app.post("/api/nota/estado")
def api_nota_estado():
    d = request.get_json(force=True)
    idx, estado = d.get("id"), d.get("estado")
    if estado not in ESTADOS:
        return jsonify({"error": "estado inválido"}), 400
    notas = cargar_notas()
    for n in notas:
        if n["_id"] == idx:
            n["estado"] = estado
            n.pop("resuelta", None)
    with open(NOTAS, "w") as f:
        for n in notas:
            n.pop("_id", None)
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})

# ---------- API: alimentar / aprobar ----------
@app.post("/api/aprender")
def api_aprender():
    autor = (request.form.get("autor") or "anónimo").strip()
    titulo = (request.form.get("titulo") or "").strip()
    contenido = (request.form.get("contenido") or "").strip()
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        import transcribir_audio
        if transcribir_audio.es_audio(archivo.filename):
            # guardar el audio temporalmente y transcribirlo
            tmp = os.path.join(DATA, "_audio_tmp" + os.path.splitext(archivo.filename)[1])
            archivo.save(tmp)
            try:
                texto = transcribir_audio.transcribir(tmp)
            except Exception as e:
                return jsonify({"error": f"no se pudo transcribir el audio: {e}"}), 500
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            if not texto:
                return jsonify({"error": "el audio no contenía voz reconocible"}), 400
            contenido = (contenido + "\n\n[Transcripción de audio]\n" + texto).strip()
            titulo = titulo or ("Audio: " + os.path.splitext(archivo.filename)[0])
        else:
            contenido = (contenido + "\n\n" + archivo.read().decode("utf-8", "ignore")).strip()
            titulo = titulo or os.path.splitext(archivo.filename)[0]
    if not contenido:
        return jsonify({"error": "no hay contenido"}), 400
    sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    nombre = f"{sello}-{_slug(titulo)}.md"
    with open(os.path.join(APORTES, nombre), "w") as f:
        f.write(f"# {titulo or 'Aporte sin título'}\n\n"
                f"_Subido por {autor} el {sello}._\n\n{contenido}\n")
    return jsonify({"ok": True, "archivo": nombre})

@app.get("/api/aportes")
def api_aportes():
    items = []
    for a in sorted(os.listdir(APORTES)):
        if a.endswith(".md"):
            txt = open(os.path.join(APORTES, a)).read()
            items.append({"archivo": a, "preview": txt[:600]})
    return jsonify(items[::-1])

@app.post("/api/aportes/aprobar")
def api_aprobar():
    _solo_admin()
    archivo = _seguro(request.get_json(force=True).get("archivo"))
    ruta = os.path.join(APORTES, archivo)
    if not os.path.exists(ruta):
        return jsonify({"error": "no existe"}), 404
    contenido = open(ruta).read()
    # se integra a la base con prefijo alto para que ordene al final
    destino = os.path.join(KB, f"90-aporte-{archivo.replace('.md','')}.md")
    with open(destino, "w") as f:
        f.write(contenido)
    os.replace(ruta, os.path.join(APROBADOS, archivo))
    chars = agente.recargar_conocimiento()  # el agente ya lo usa de inmediato
    return jsonify({"ok": True, "integrado": os.path.basename(destino), "kb_chars": chars})

@app.post("/api/aportes/rechazar")
def api_rechazar():
    _solo_admin()
    archivo = _seguro(request.get_json(force=True).get("archivo"))
    ruta = os.path.join(APORTES, archivo)
    if not os.path.exists(ruta):
        return jsonify({"error": "no existe"}), 404
    os.replace(ruta, os.path.join(RECHAZADOS, archivo))
    return jsonify({"ok": True})

@app.get("/api/conocimiento")
def api_conocimiento():
    files = sorted(f for f in os.listdir(KB) if f.endswith(".md"))
    return jsonify(files)

@app.get("/")
def home():
    # no-store: que el navegador siempre cargue la versión más reciente tras un despliegue
    return Response(PAGINA, mimetype="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})


PAGINA = r"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard — Agente de Froy</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
  theme: {
    extend: {
      colors: { brand: { DEFAULT:'#b5261e', light:'#d8453b', dark:'#2a0e0c' } },
      maxWidth: { '8xl':'1440px', '9xl':'1680px' }
    }
  }
}
</script>
<style>
:root{
  --bg:#f4f1f0;--card:#fff;--ink:#1a1210;--muted:#8f827f;--line:#efe6e4;
  --grad1:#b5261e;--grad2:#d8453b;--g1:var(--grad1);--g2:var(--grad2);
  --dark:#2a0e0c;--darker:#180706;--green:#16a34a;--amber:#ffb020;--red:#ff5d5d;
  --pillbg:#fbe6e4;--pilltext:#96201a;
  --shadow:0 10px 30px rgba(30,15,12,.07);--radius:24px;
}
*{box-sizing:border-box;font-family:'Inter',-apple-system,Segoe UI,Roboto,sans-serif}
body{margin:0;background:#e7e8e1;color:var(--ink)}
.app{display:flex;min-height:calc(100vh - 40px);margin:20px;background:#fff;border-radius:28px;box-shadow:0 20px 60px rgba(20,30,20,.10);overflow:hidden}
.side{width:240px;padding:24px 16px;display:flex;flex-direction:column;gap:3px;position:relative;background:var(--card)}
.navgrp{font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#b4b8ab;padding:16px 14px 6px}
.logo{font-weight:800;font-size:19px;padding:6px 12px 20px;display:flex;align-items:center;gap:9px;letter-spacing:-.02em}
.dot{width:26px;height:26px;border-radius:50%;background:linear-gradient(135deg,var(--grad1),var(--grad2));box-shadow:0 0 0 5px #b5261e1a;position:relative}
.dot::after{content:"";position:absolute;inset:6px;border-radius:50%;background:#fff;opacity:.35}
.nav{background:none;border:0;text-align:left;padding:12px 16px;border-radius:999px;font-size:14px;font-weight:600;color:#84887c;cursor:pointer;display:flex;gap:10px;align-items:center;position:relative}
.nav:hover{color:var(--ink);background:#f2f3ee}
.nav.active{background:var(--ink);color:#fff;box-shadow:var(--shadow)}
.nav.active::before{content:"";position:absolute;left:-16px;top:50%;transform:translateY(-50%);width:4px;height:22px;background:var(--grad1);border-radius:0 4px 4px 0}
.main{flex:1;padding:28px 32px 60px;background:var(--bg)}
.topbar{display:flex;align-items:center;gap:16px;margin-bottom:26px}
.searchbox{flex:1;display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:12px 18px;color:var(--muted);font-size:13px;box-shadow:var(--shadow)}
.searchbox input{border:0!important;background:none!important;padding:0!important;margin:0!important;border-radius:0!important;flex:1;font-size:13px;width:auto}
.searchbox input:focus{outline:0}
.who{display:flex;align-items:center;gap:11px}
.avatar{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--grad1),var(--grad2));color:#fff;display:grid;place-items:center;font-weight:800;font-size:15px;flex-shrink:0}
.who .nm{font-weight:700;font-size:13.5px;line-height:1.3}.who .rl{color:var(--muted);font-size:11.5px}
h1{font-size:34px;margin:0 0 4px;font-weight:800;letter-spacing:-.03em}.sub{color:var(--muted);font-size:13.5px;margin-bottom:24px}
.bento{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}
.card{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);padding:24px}
.c2{grid-column:span 2}.c4{grid-column:span 4}
.stat .lbl{color:#57594f;font-size:14.5px;font-weight:600}
.stat .num{font-size:46px;font-weight:800;margin-top:12px;letter-spacing:-.02em;line-height:1}
.stat .pill{display:inline-block;margin-top:12px;font-size:12.5px;font-weight:700;padding:4px 11px;border-radius:20px}
.up{background:var(--pillbg);color:var(--pilltext)}.warn{background:#ffb02018;color:#b9791a}
.statbtn{width:34px;height:34px;border-radius:50%;border:1px solid var(--line);display:grid;place-items:center;float:right;color:var(--ink)}
.card.stat.featured{background:radial-gradient(120% 140% at 0% 0%,var(--dark),var(--darker));color:#fff}
.card.stat.featured .lbl{color:#c8d6ca}
.card.stat.featured .pill.up{background:#ffffff1f;color:#7fe3a3}
.card.stat.featured .statbtn{border-color:#ffffff30;color:#fff}
.h{font-weight:800;font-size:16.5px;letter-spacing:-.01em;margin:0 0 16px;display:flex;justify-content:space-between;align-items:center}
.bar{display:flex;align-items:center;gap:10px;margin:9px 0;font-size:13px}
.bar .nm{width:92px;color:var(--muted);text-transform:capitalize}
.bar .track{flex:1;height:10px;background:#eeefe8;border-radius:999px;overflow:hidden}
.bar .fill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--grad1),var(--grad2))}
.bar .vv{width:30px;text-align:right;font-weight:700}
.donut{width:140px;height:140px;border-radius:50%;display:grid;place-items:center;margin:0 auto}
.donut .inner{width:102px;height:102px;background:var(--card);border-radius:50%;display:grid;place-items:center;text-align:center}
.geo{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.geo b{font-weight:700}
.tag{font-size:11px;padding:2px 8px;border-radius:20px;background:#eef;color:#55c;font-weight:700}
.tag.q{background:#fdeaea;color:#d44}.tag.s{background:#fff3e0;color:#c80}.tag.a{background:#e8f8f0;color:#0a8}
/* tabla de contactos */
.ctable{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
.ctable th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:700;padding:8px 12px;border-bottom:1px solid var(--line)}
.ctable td{padding:10px 12px;border-bottom:1px solid var(--line)}
.ctable tr:last-child td{border-bottom:0}
.postura{font-size:11px;padding:3px 10px;border-radius:20px;font-weight:700;display:inline-block}
.postura.simpatizante{background:#dcfce7;color:#15803d}
.postura.neutral{background:#eef0e9;color:#63665a}
.postura.opositor{background:#fdeaea;color:#c0392b}
.postura.sin_datos{background:#f2f3ee;color:#a5a89c}
/* chat */
.chatwrap{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}
.chat{height:60vh;overflow-y:auto;padding:6px}
.b{max-width:84%;padding:9px 12px;border-radius:14px;margin:7px 0;font-size:14px;line-height:1.4;white-space:pre-wrap}
.bot{background:#f3f4fa;border-top-left-radius:3px}
.yo{background:linear-gradient(135deg,var(--grad1),var(--grad2));color:#fff;margin-left:auto;border-top-right-radius:3px}
.meta{font-size:11px;color:var(--muted);margin:2px 0 8px}
.lnk{font-size:11px;color:#0a8;cursor:pointer}
.row{display:flex;gap:8px}.row input{flex:1}
input,textarea,select{width:100%;padding:12px 14px!important;border:1px solid var(--line)!important;border-radius:14px!important;font-size:14px;font-family:inherit;background:#fbfcfe;margin:0 0 10px!important;display:block}
textarea{min-height:90px;resize:vertical}
.row input,.row select{margin-bottom:0!important}
button.b1{background:var(--dark);color:#fff;border:0;border-radius:999px;padding:12px 22px;font-weight:700;font-size:13.5px;cursor:pointer}
button.b1:hover{background:linear-gradient(135deg,var(--grad1),var(--grad2))}
button.ghost{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:10px 18px;font-weight:600;font-size:13.5px;cursor:pointer}
button.ok{background:#19c37d;color:#fff}button.no{background:#ff5d5d;color:#fff}
.aporte{border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:12px}
.aporte pre{white-space:pre-wrap;font-size:12px;color:#555;background:#fafbff;padding:10px;border-radius:10px;max-height:140px;overflow:auto;font-family:inherit}
.nota{border-left:4px solid var(--grad1);background:#fafbff;border-radius:0 12px 12px 0;padding:12px 14px;margin-bottom:10px}
.nota.done{opacity:.5}
.muted{color:var(--muted);font-size:13px}
.hide{display:none}
.flash{font-size:13px;color:#0a8;min-height:18px;margin-top:6px}
/* --- componentes nuevos --- */
.row2{display:flex;gap:8px;flex-wrap:wrap}.row2 select{flex:1;min-width:130px}
.notaform{background:#f7f9ff;border:1px solid var(--line);border-radius:14px;padding:10px;margin:6px 0}
.ideal-box{background:#eaf4ff;border-radius:10px;padding:8px 10px;font-size:13px;color:#0a6aa8;margin:6px 0}
.drop{border:2px dashed #ccd5ea;border-radius:16px;padding:26px 16px;text-align:center;color:#8a93a6;cursor:pointer;transition:.15s;background:#fafbff;margin:8px 0}
.drop.over{border-color:var(--g1);background:#e8f8ee;color:var(--grad1);transform:scale(1.01)}
.drop u{color:var(--grad1)}.drop .file{margin-top:8px;font-weight:700;color:var(--ink)}
.vbars{display:flex;align-items:flex-end;gap:8px;height:170px;padding-top:14px}
.vbars .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:6px}
.vbars .bar2{width:100%;max-width:34px;background:linear-gradient(180deg,var(--grad2),var(--grad1));border-radius:999px;min-height:24px;transition:.2s}
.vbars .bar2.empty{background:repeating-linear-gradient(45deg,#e7e8e0,#e7e8e0 4px,#f2f3ee 4px,#f2f3ee 8px)}
.vbars .col:hover .bar2{filter:brightness(1.08)}
.vbars .d{font-size:10px;color:var(--muted);font-weight:600}.vbars .v{font-size:10px;font-weight:700;color:var(--grad1)}
#mapa-canvas{height:62vh;border-radius:16px;z-index:1}
.aporte.done{opacity:.55}
.conv{border-bottom:1px solid var(--line);padding:10px 0;font-size:13px}
.conv>div{margin:2px 0}
.barbusq{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}.barbusq input{flex:1;min-width:160px}.barbusq select{width:auto}
#kanban{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;align-items:start}
.kcol{background:#f4f6fb;border-radius:14px;padding:10px;min-height:120px;transition:.12s}
.kcol.over{outline:2px dashed var(--g1);background:#e8f8ee}
.kcol h4{margin:0 0 8px;font-size:13px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
.kcard{background:#fff;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,.06);padding:10px;margin-bottom:8px;cursor:grab}
.kcard:active{cursor:grabbing}
.progbar{height:14px;background:#f0f2f8;border-radius:10px;overflow:hidden}
.progbar>div{height:100%;background:linear-gradient(90deg,var(--g1),var(--g2));transition:.3s}
@media(max-width:640px){#kanban{grid-template-columns:1fr}}
/* tarjetas de redes / kanban bonitas */
.rcard{background:#fff;border-radius:16px;box-shadow:0 6px 18px rgba(30,40,90,.07);margin-bottom:14px;overflow:hidden;border:1px solid var(--line);transition:.15s}
.rcard:hover{box-shadow:0 10px 26px rgba(30,40,90,.13);transform:translateY(-1px)}
.rcard .top{height:5px}
.rcard .body{padding:13px 15px}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:700;padding:3px 10px;border-radius:20px;white-space:nowrap}
.chip.net{color:#fff}
.chip.acc{background:#eef0f7;color:#3a4256}
.chip.perf{background:#e8f8ee;color:var(--grad1)}
.chip.st{background:#fff3e0;color:#c8770a}.chip.st.ok{background:#e7f8ef;color:#0a8a52}
.combox{background:#f6f8ff;border-left:3px solid var(--g1);border-radius:0 10px 10px 0;padding:9px 11px;font-size:13px;color:#3a4256;margin:10px 0;line-height:1.4}
.rowbtns{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px;align-items:center}
.btnmini{border:1px solid var(--line);background:#fff;border-radius:10px;padding:7px 12px;font-size:12.5px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px;color:#3a4256;text-decoration:none;transition:.12s}
.btnmini:hover{background:#f4f6fb;border-color:#d5dbea}
.btnmini.pri{background:linear-gradient(135deg,var(--g1),var(--g2));color:#fff;border:0}
.btnmini.ok{color:#0a8a52;border-color:#bfe9d3}.btnmini.dgr{color:#d9534f;border-color:#f2c9c7}
.evgrid{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.evgrid img{width:56px;height:56px;object-fit:cover;border-radius:9px;border:1px solid var(--line)}
/* modal */
.modal{position:fixed;inset:0;background:rgba(20,26,45,.45);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px}
.modal.hide{display:none}
.modalbox{background:#fff;border-radius:20px;padding:22px;width:min(480px,94vw);max-height:92vh;overflow:auto;box-shadow:0 30px 60px rgba(0,0,0,.3)}
.modalbox label{font-size:12px;font-weight:700;color:#8a93a6;display:block;margin:10px 0 3px}
select{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238a93a6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center;padding-right:28px}
/* --- tablet --- */
@media(max-width:980px){.bento{grid-template-columns:repeat(2,1fr)}.c4{grid-column:span 2}.chatwrap{grid-template-columns:1fr}.app{flex-direction:column}
  .side{width:auto;flex-direction:row;overflow-x:auto;align-items:center;gap:6px;position:sticky;top:0;z-index:9;background:var(--bg);box-shadow:0 2px 8px rgba(0,0,0,.04)}.side>div:last-child{min-width:160px}
  .side .navgrp{display:none}}
/* --- teléfono --- */
@media(max-width:640px){
  .app{margin:0;border-radius:0;min-height:100vh}
  .main{padding:16px 13px 60px}
  h1{font-size:20px}.sub{margin-bottom:16px}
  .bento{grid-template-columns:1fr;gap:12px}.c2,.c4{grid-column:span 1}
  .card{padding:16px;border-radius:18px}
  .logo{display:none}
  .nav{padding:10px 13px;font-size:14px;white-space:nowrap}
  .stat .num{font-size:28px}
  .topbar{flex-wrap:wrap;gap:10px}
  .searchbox{order:2;flex-basis:100%}
  .who .rl{display:none}
  .chat{height:56vh}
  input,textarea,select,button{font-size:16px}      /* evita el zoom de iOS al enfocar */
  button.b1,button.ghost{padding:13px 18px}          /* objetivos táctiles más grandes */
  .b{max-width:90%}.donut{margin:0}
}
</style></head><body>
<div class="app">
  <div class="side">
    <div class="logo"><span class="dot"></span> Froy · Panel</div>
    <div class="navgrp">Panel</div>
    <button class="nav active" data-t="resumen"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>Resumen</button>
    <button class="nav" data-t="seguimiento"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><rect x="5" y="4" width="14" height="17" rx="2"/><rect x="8" y="2" width="8" height="4" rx="1"/><line x1="8" y1="11" x2="16" y2="11"/><line x1="8" y1="15" x2="16" y2="15"/></svg>Seguimiento</button>
    <button class="nav" data-t="redes"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><path d="M3 9v6h3l10 4V5L6 9H3z"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>Redes</button>
    <div class="navgrp">Ciudadanía</div>
    <button class="nav" data-t="mapa"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/></svg>Mapa</button>
    <button class="nav" data-t="tendencias"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></svg>Tendencias</button>
    <button class="nav" data-t="escalados"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><path d="M3 12h4l2 3h6l2-3h4"/><rect x="3" y="5" width="18" height="14" rx="2"/></svg>Escalados</button>
    <button class="nav" data-t="explorar"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>Explorar</button>
    <button class="nav" data-t="contactos"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 21c0-3.5 3-5.5 6.5-5.5s6.5 2 6.5 5.5"/><circle cx="17" cy="8.5" r="2.6"/><path d="M15 15.6c2.8.4 4.5 2.3 4.5 5.4"/></svg>Contactos</button>
    <div class="navgrp">Bot</div>
    <button class="nav" data-t="pruebas"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><path d="M9 2v6L4 19a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3L15 8V2"/><line x1="8" y1="2" x2="16" y2="2"/><line x1="6" y1="15" x2="18" y2="15"/></svg>Pruebas</button>
    <button class="nav" data-t="alimentar"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><path d="M4 5a2 2 0 0 1 2-2h6v18H6a2 2 0 0 1-2-2V5z"/><path d="M20 5a2 2 0 0 0-2-2h-6v18h6a2 2 0 0 0 2-2V5z"/></svg>Alimentar</button>
    <button class="nav" data-t="notas"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><circle cx="12" cy="9" r="6"/><path d="M9 20h6M10 22h4M9 15h6"/></svg>Notas</button>
    <button class="nav" data-t="auditoria"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><line x1="12" y1="3" x2="12" y2="21"/><path d="M5 7h14"/><path d="M5 7 2 13a3 3 0 0 0 6 0L5 7z"/><path d="M19 7l-3 6a3 3 0 0 0 6 0l-3-6z"/><path d="M8 21h8"/></svg>Auditoría</button>
    <div class="navgrp">Admin</div>
    <button class="nav" data-t="conexion"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>Conexión</button>
    <button class="nav" data-t="usuarios"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-2 align-[-4px] shrink-0"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 21c0-3.5 3-5.5 6.5-5.5s6.5 2 6.5 5.5"/><circle cx="17" cy="8.5" r="2.6"/><path d="M15 15.6c2.8.4 4.5 2.3 4.5 5.4"/></svg>Usuarios</button>
    <div style="flex:1"></div>
    <div class="muted" style="padding:12px">Tu nombre:<br><input id="autor" placeholder="quién administra" style="margin-top:6px"></div>
  </div>
  <div class="main">
   <div class="mx-auto w-full max-w-[1300px] 2xl:max-w-[1600px]">
    <div class="topbar">
      <div class="searchbox"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg><input id="topsearch" placeholder="Buscar sección… (Enter)" onkeydown="if(event.key==='Enter')buscarSeccion(this.value)"></div>
      <div class="who"><div class="avatar" id="avatarletra">·</div><div><div class="nm" id="whoami">…</div><div class="rl">Panel de campaña</div></div></div>
    </div>

    <!-- RESUMEN -->
    <section id="resumen">
      <h1>Resumen</h1><div class="sub">Estadísticas y segmentación territorial de las conversaciones</div>
      <div class="bento" id="cards"></div>
      <div class="bento" style="margin-top:18px">
        <div class="card c2"><div class="h">Tipo de mensaje</div><div id="tipos"></div></div>
        <div class="card c2"><div class="h">Escalados a coordinador</div>
          <div style="display:flex;gap:18px;align-items:center">
            <div class="donut" id="donut"><div class="inner"><div><b id="dpct" style="font-size:24px">0%</b><br><span class="muted">escalado</span></div></div></div>
            <div class="muted" id="dtext"></div>
          </div></div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><path d="M12 21s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="9" r="2.3"/></svg>Top municipios</div><div id="munis"></div></div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><rect x="4" y="3" width="10" height="18"/><rect x="15" y="9" width="6" height="12"/></svg>Top colonias</div><div id="cols"></div></div>
        <div class="card c4"><div class="h">Últimas conversaciones</div><div id="recientes"></div></div>
      </div>
    </section>

    <!-- PRUEBAS -->
    <section id="pruebas" class="hide">
      <h1>Pruebas</h1><div class="sub">Chatea con el agente y anota mejoras sobre cualquier respuesta</div>
      <div class="chatwrap">
        <div class="card"><div class="chat" id="chat"></div>
          <div class="row" style="margin-top:10px"><input id="msg" placeholder="Escribe como ciudadano…"><button class="b1" onclick="enviar()">Enviar</button></div>
          <label class="muted" style="display:flex;gap:6px;margin-top:8px"><input type="checkbox" id="sim" style="width:auto" checked> simular tiempos humanos (escribiendo… y globos)</label>
        </div>
        <div class="card"><div class="h">¿Cómo leer esto?</div>
          <p class="muted">Cada respuesta del bot se muestra en globos (como en WhatsApp) y debajo el <b>meta</b> que captura: tipo, colonia, municipio y si escala. Usa “anotar mejora” para dejar feedback que verás en la pestaña Notas.</p>
          <div class="h" style="margin-top:18px">Base de conocimiento activa</div><div id="kb" class="muted"></div>
        </div>
      </div>
    </section>

    <!-- ALIMENTAR -->
    <section id="alimentar" class="hide">
      <h1>Alimentar</h1><div class="sub">Sube información y aprueba qué aprende el agente. Aprobar lo integra de inmediato.</div>
      <div class="bento">
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Subir información</div>
          <input id="t_titulo" placeholder="Título (ej. Logros en salud)">
          <textarea id="t_cont" placeholder="Pega aquí la información…"></textarea>
          <div class="drop" id="drop">
            <input type="file" id="t_file" accept=".txt,.md,.mp3,.m4a,.ogg,.opus,.wav,.aac,.amr,.aiff,.flac,.webm,audio/*" hidden>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mb-1"><path d="M21 11 12 20a4 4 0 0 1-5.7-5.7L15 5.6a2.6 2.6 0 0 1 3.7 3.7L10 18a1.2 1.2 0 0 1-1.7-1.7l7.6-7.6"/></svg><br>Arrastra un archivo o audio aquí, o <u>toca para elegir</u>
            <div style="font-size:12px;margin-top:4px;color:#aab">texto (.txt, .md) o audio (.mp3, .m4a, .ogg…) — los audios se transcriben solos</div>
            <div class="file" id="dropname"></div>
          </div>
          <button class="b1" onclick="aprender()">Enviar aporte</button>
          <div class="flash" id="f_aprender"></div>
        </div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>Pendientes de aprobar <span class="tag" id="npend">0</span></div>
          <div id="pendientes"><span class="muted">Cargando…</span></div>
        </div>
      </div>
    </section>

    <!-- TENDENCIAS -->
    <section id="tendencias" class="hide">
      <h1>Tendencias</h1><div class="sub">Qué pide la gente y cómo cambia en el tiempo</div>
      <div class="bento">
        <div class="card c4"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/></svg>Conversaciones por día <span class="muted">· últimos 14 días</span></div><div id="serie"></div></div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><path d="M12 2c1 3-2 4-2 7a4 4 0 0 0 8 0c0-2-1-3-1-3s2 1 2 5a7 7 0 0 1-14 0c0-5 4-6 4-9 0 0 2 0 3 0z"/></svg>Temas más frecuentes</div><div id="temasfreq"></div></div>
        <div class="card c2"><div class="h">¿Para qué sirve?</div>
          <p class="muted">Te muestra qué preocupa más a la gente (agua, becas, seguridad…) y si hay picos en una zona o tema. Útil para enfocar el mensaje y detectar problemas que están subiendo. Se nutre de cada conversación, real o de prueba.</p></div>
      </div>
    </section>

    <!-- NOTAS -->
    <section id="notas" class="hide">
      <h1>Notas de mejora</h1><div class="sub">Lo que el equipo sugiere al probar</div>
      <div class="card c4"><div id="listanotas"><span class="muted">Cargando…</span></div></div>
    </section>

    <!-- MAPA -->
    <section id="mapa" class="hide">
      <h1>Mapa territorial</h1><div class="sub">De dónde escribe la gente, por municipio</div>
      <div class="card c4"><div id="mapa-canvas"></div><div class="muted" id="mapa-sin" style="margin-top:10px"></div></div>
    </section>

    <!-- ESCALADOS -->
    <section id="escalados" class="hide">
      <h1>Bandeja de escalados</h1><div class="sub">Mensajes que necesitan seguimiento del coordinador <span class="tag" id="esc-count"></span></div>
      <div class="card c4"><div id="esc-list"><span class="muted">Cargando…</span></div></div>
    </section>

    <!-- EXPLORAR -->
    <section id="explorar" class="hide">
      <h1>Explorar conversaciones</h1><div class="sub">Busca, filtra y exporta <span class="tag" id="ex-count"></span></div>
      <div class="card c4">
        <div class="barbusq">
          <input id="ex-q" placeholder="Buscar en mensajes, colonia, municipio…">
          <select id="ex-tipo"><option value="">Todos los tipos</option><option>apoyo</option><option>personaje</option><option>4t</option><option>solicitud</option><option>queja</option><option>otro</option></select>
          <button class="ghost" onclick="cargarExplorar()">Buscar</button>
          <a class="b1" href="/export.csv" style="text-decoration:none;display:inline-block;padding:11px 16px;border-radius:12px">⬇ Exportar CSV</a>
        </div>
        <div id="ex-list"><span class="muted">Cargando…</span></div>
      </div>
    </section>

    <!-- CONTACTOS (perfil por número, se arma solo con el tiempo) -->
    <section id="contactos" class="hide">
      <h1>Contactos</h1><div class="sub">Perfil de cada número que ha escrito — se arma solo, mensaje a mensaje. El bot usa estos datos como contexto para no volver a preguntarlos <span class="tag" id="ct-count"></span></div>
      <div class="card c4">
        <div class="barbusq">
          <input id="ct-q" placeholder="Buscar por número, nombre, ciudad, colonia, tema…" onkeydown="if(event.key==='Enter')cargarContactos()">
          <select id="ct-postura" onchange="cargarContactos()">
            <option value="">Todas las posturas</option>
            <option value="simpatizante">Simpatizante</option>
            <option value="neutral">Neutral</option>
            <option value="opositor">Opositor</option>
            <option value="sin_datos">Sin datos suficientes</option>
          </select>
          <button class="ghost" onclick="cargarContactos()">Buscar</button>
        </div>
        <div style="overflow-x:auto">
        <table class="ctable">
          <thead><tr><th>Número</th><th>Nombre</th><th>Ciudad</th><th>Colonia</th><th>Tema</th><th>Postura</th><th>Msjs</th><th>Última actividad</th></tr></thead>
          <tbody id="ct-body"><tr><td colspan="8"><span class="muted">Cargando…</span></td></tr></tbody>
        </table>
        </div>
      </div>
    </section>

    <!-- SEGUIMIENTO (kanban de publicaciones) -->
    <section id="seguimiento" class="hide">
      <h1>Seguimiento</h1><div class="sub">Tablero de publicaciones — arrastra o usa ◀ ▶ para mover el estado</div>
      <div class="bento">
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/></svg>Meta y avance</div>
          <div class="progbar"><div id="seg-bar" style="width:0"></div></div>
          <div class="muted" id="seg-metanum" style="margin-top:8px"></div>
          <div id="seg-metaform" style="margin-top:10px">
            <div class="barbusq"><input id="seg-mtexto" placeholder="Meta (ej. 100 comentarios esta semana)"><input id="seg-mobj" type="number" placeholder="Objetivo" style="width:110px;flex:none"><button class="ghost" onclick="guardarMeta()">Guardar</button></div>
          </div>
        </div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><path d="M8 3h8v4a4 4 0 0 1-8 0V3z"/><path d="M6 4H4a3 3 0 0 0 3 5"/><path d="M18 4h2a3 3 0 0 1-3 5"/><path d="M10 14h4v3h-4z"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>Actividad por persona <span class="muted" style="font-weight:400">· evidencias subidas</span></div>
          <div id="seg-persona"></div>
        </div>
      </div>
      <div id="kanban" style="margin-top:16px"><span class="muted">Cargando…</span></div>
    </section>

    <!-- REDES (war room digital) -->
    <section id="redes" class="hide">
      <h1>Acciones en redes</h1><div class="sub">Coordinación del equipo: qué comentar, qué reaccionar, con qué perfil y en qué link <span class="tag" id="red-pend"></span></div>
      <div class="bento">
        <div class="card c2" id="red-form-card"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Nueva acción</div>
          <select id="r-red"><option value="Facebook">Facebook</option><option>Instagram</option><option>Otro</option></select>
          <input id="r-link" placeholder="Pega aquí el link del post…">
          <select id="r-accion"><option>Comentar</option><option>Dar like</option><option>Reacción</option><option>Compartir</option></select>
          <select id="r-perfil"><option value="">Tipo de perfil (quién debe reaccionar)…</option><option>Ciudadano común</option><option>Joven</option><option>Maestra/o</option><option>Mamá/Papá de familia</option><option>Simpatizante</option><option>Comerciante</option><option>Adulto mayor</option><option>Cualquiera</option></select>
          <textarea id="r-com" placeholder="¿Qué comentar? (texto sugerido para copiar y pegar)"></textarea>
          <button class="b1" onclick="addAccion()">Publicar acción</button>
          <div class="flash" id="r-flash"></div>
        </div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><rect x="5" y="4" width="14" height="17" rx="2"/><rect x="8" y="2" width="8" height="4" rx="1"/><line x1="8" y1="11" x2="16" y2="11"/><line x1="8" y1="15" x2="16" y2="15"/></svg>Acciones del equipo</div>
          <div id="red-list"><span class="muted">Cargando…</span></div>
        </div>
      </div>
    </section>

    <!-- AUDITORÍA -->
    <section id="auditoria" class="hide">
      <h1>Auditoría de cumplimiento</h1><div class="sub">Cuándo el bot frenó algo por las reglas legales (vota, gobernador, promesas…)</div>
      <div class="card c4"><div id="aud-list"><span class="muted">Cargando…</span></div></div>
    </section>

    <!-- CONEXIÓN (Green API) -->
    <section id="conexion" class="hide">
      <h1>Conexión de WhatsApp</h1><div class="sub">Conecta el bot con Green API (WhatsApp por QR, sin Meta)</div>
      <div class="bento">
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>Credenciales de Green API</div>
          <p class="muted" style="font-size:13px">Sácalas de tu consola de <b>green-api.com</b> (idInstance y apiTokenInstance).</p>
          <label style="font-size:12px;font-weight:700;color:#8a93a6">idInstance</label>
          <input id="g-id" placeholder="Ej. 1101000001">
          <label style="font-size:12px;font-weight:700;color:#8a93a6">apiTokenInstance</label>
          <input id="g-token" placeholder="Ej. abcdef123456...">
          <label style="font-size:12px;font-weight:700;color:#8a93a6">apiUrl (déjalo así si no sabes)</label>
          <input id="g-url" placeholder="https://api.green-api.com">
          <div style="display:flex;gap:8px;margin-top:10px"><button class="b1" onclick="guardarGreen()">Guardar</button><button class="ghost" onclick="probarGreen()">Probar conexión</button></div>
          <div class="flash" id="g-flash"></div>
        </div>
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><line x1="4" y1="20" x2="4" y2="14"/><line x1="10" y1="20" x2="10" y2="10"/><line x1="16" y1="20" x2="16" y2="6"/><line x1="22" y1="20" x2="22" y2="3"/></svg>Estado y webhook</div>
          <div id="g-estado" style="font-size:14px;margin-bottom:12px"></div>
          <p class="muted" style="font-size:13px;margin-bottom:4px">En la consola de Green API, en <b>Configuración → webhookUrl</b>, pega esta URL para que los mensajes lleguen al bot:</p>
          <div style="background:#f5f7ff;border-radius:10px;padding:10px;font-size:13px;word-break:break-all" id="g-webhook"></div>
          <div class="h" style="margin-top:18px">¿Cómo conectar?</div>
          <ol class="muted" style="font-size:13px;padding-left:18px;line-height:1.6">
            <li>Crea una instancia en green-api.com y <b>escanea el QR</b> con el WhatsApp del bot.</li>
            <li>Copia idInstance y apiTokenInstance aquí → <b>Guardar</b>.</li>
            <li>Pega la <b>webhookUrl</b> de arriba en la consola de Green API.</li>
            <li>Activa <b>incomingWebhook</b> en Green API. ¡Listo, el bot ya contesta!</li>
          </ol>
        </div>
      </div>
    </section>

    <!-- USUARIOS -->
    <section id="usuarios" class="hide">
      <h1>Usuarios y roles</h1><div class="sub">admin = todo · coordinador = sin gestión de usuarios · brigadista = solo pruebas y notas</div>
      <div class="bento">
        <div class="card c2"><div class="h"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Nuevo usuario</div>
          <input id="u-user" placeholder="usuario">
          <input id="u-pass" placeholder="contraseña">
          <select id="u-rol"><option value="brigadista">brigadista</option><option value="coordinador">coordinador</option><option value="admin">admin</option></select>
          <button class="b1" onclick="addUser()" style="margin-top:8px">Crear usuario</button>
          <div class="flash" id="u-flash"></div>
        </div>
        <div class="card c2"><div class="h">Usuarios del equipo</div><div id="us-list"><span class="muted">Cargando…</span></div></div>
      </div>
    </section>

   </div>
  </div>
</div>
<div id="editmodal" class="modal hide">
  <div class="modalbox">
    <div class="h" style="font-size:16px;margin-bottom:4px"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block mr-1.5 align-[-3px] shrink-0"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>Editar publicación</div>
    <input type="hidden" id="e-id">
    <label>Red social</label>
    <select id="e-red"><option>Facebook</option><option>Instagram</option><option>Otro</option></select>
    <label>Link del post</label>
    <input id="e-link" placeholder="https://…">
    <label>Acción</label>
    <select id="e-accion"><option>Comentar</option><option>Dar like</option><option>Reacción</option><option>Compartir</option></select>
    <label>Tipo de perfil</label>
    <select id="e-perfil"><option value="">—</option><option>Ciudadano común</option><option>Joven</option><option>Maestra/o</option><option>Mamá/Papá de familia</option><option>Simpatizante</option><option>Comerciante</option><option>Adulto mayor</option><option>Cualquiera</option></select>
    <label>Comentario / indicación</label>
    <textarea id="e-com"></textarea>
    <div style="margin-top:14px;display:flex;gap:8px"><button class="b1" onclick="guardarEdit()">Guardar cambios</button><button class="ghost" onclick="cerrarEdit()">Cancelar</button></div>
  </div>
</div>
<script>
function svg(inner,size){size=size||16;return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline-block align-[-3px]">${inner}</svg>`;}
const ICONS={
  chat:svg('<path d="M4 5h16v10H8l-4 4V5z"/>'),
  flag:svg('<path d="M5 3v18M5 4h11l-2 4 2 4H5"/>'),
  bulb:svg('<circle cx="12" cy="9" r="6"/><path d="M9 20h6M10 22h4M9 15h6"/>'),
  book:svg('<path d="M4 5a2 2 0 0 1 2-2h6v18H6a2 2 0 0 1-2-2V5z"/><path d="M20 5a2 2 0 0 0-2-2h-6v18h6a2 2 0 0 0 2-2V5z"/>'),
  up:svg('<polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/>',12),
  phone:svg('<rect x="7" y="2" width="10" height="20" rx="2"/><line x1="11" y1="18" x2="13" y2="18"/>'),
  undo:svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-2"/>'),
  check:svg('<polyline points="20 6 9 17 4 12"/>'),
  sparkles:svg('<path d="M12 3v4M12 17v4M4 12h4M16 12h4M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3"/>'),
  user:svg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
  ban:svg('<circle cx="12" cy="12" r="9"/><line x1="6" y1="6" x2="18" y2="18"/>'),
  checkcircle:svg('<circle cx="12" cy="12" r="9"/><polyline points="8 12 11 15 16 9"/>'),
  warn:svg('<path d="M12 3 2 20h20L12 3z"/><line x1="12" y1="10" x2="12" y2="14"/><line x1="12" y1="17" x2="12.01" y2="17"/>'),
  facebook:svg('<path d="M14 9h3V5h-3a4 4 0 0 0-4 4v2H7v4h3v7h4v-7h3l1-4h-4V9a1 1 0 0 1 1-1z"/>',13),
  camera:svg('<path d="M4 8h3l2-3h6l2 3h3v11H4V8z"/><circle cx="12" cy="13" r="3.5"/>',13),
  globe:svg('<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><line x1="3" y1="12" x2="21" y2="12"/>',13),
  thumbup:svg('<path d="M7 22V11l4-8 1 1v6h6l-2 12H7z"/>',13),
  heart:svg('<path d="M12 21c-5-4-9-7.5-9-11.5A5 5 0 0 1 12 6a5 5 0 0 1 9 3.5C21 13.5 17 17 12 21z"/>',13),
  share:svg('<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',13),
  edit:svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',14),
  trash:svg('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',14),
  link:svg('<circle cx="8" cy="16" r="3"/><circle cx="16" cy="8" r="3"/><line x1="10.5" y1="13.5" x2="13.5" y2="10.5"/>',14),
  clip:svg('<path d="M21 11 12 20a4 4 0 0 1-5.7-5.7L15 5.6a2.6 2.6 0 0 1 3.7 3.7L10 18a1.2 1.2 0 0 1-1.7-1.7l7.6-7.6"/>',14),
  tag:svg('<path d="M3 11V4h7l10 10-7 7L3 11z"/><circle cx="7.5" cy="7.5" r="1.2"/>',13),
  doc:svg('<path d="M6 2h9l5 5v15H6V2z"/><path d="M15 2v5h5"/>',13),
  xmark:svg('<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',13),
  plus:svg('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',14),
  calendar:svg('<rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/>'),
  fire:svg('<path d="M12 2c1 3-2 4-2 7a4 4 0 0 0 8 0c0-2-1-3-1-3s2 1 2 5a7 7 0 0 1-14 0c0-5 4-6 4-9 0 0 2 0 3 0z"/>'),
  target:svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/>'),
  trophy:svg('<path d="M8 3h8v4a4 4 0 0 1-8 0V3z"/><path d="M6 4H4a3 3 0 0 0 3 5"/><path d="M18 4h2a3 3 0 0 1-3 5"/><path d="M10 14h4v3h-4z"/><path d="M8 21h8"/><path d="M12 17v4"/>'),
  clipboard:svg('<rect x="5" y="4" width="14" height="17" rx="2"/><rect x="8" y="2" width="8" height="4" rx="1"/><line x1="8" y1="11" x2="16" y2="11"/><line x1="8" y1="15" x2="16" y2="15"/>'),
  mappin:svg('<path d="M12 21s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="9" r="2.3"/>',13),
  building:svg('<rect x="4" y="3" width="10" height="18"/><rect x="15" y="9" width="6" height="12"/>',13),
  signal:svg('<line x1="4" y1="20" x2="4" y2="14"/><line x1="10" y1="20" x2="10" y2="10"/><line x1="16" y1="20" x2="16" y2="6"/><line x1="22" y1="20" x2="22" y2="3"/>',13),
  bolt:svg('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',13),
  users:svg('<circle cx="9" cy="8" r="3.2"/><path d="M2.5 21c0-3.5 3-5.5 6.5-5.5s6.5 2 6.5 5.5"/><circle cx="17" cy="8.5" r="2.6"/><path d="M15 15.6c2.8.4 4.5 2.3 4.5 5.4"/>'),
  dot:c=>`<span class="inline-block w-2 h-2 rounded-full align-[1px]" style="background:${c}"></span>`,
  pin:svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>',13),
  clock:svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',13),
  arrowup:svg('<line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/>',16),
};
const COLICONS={porhacer:ICONS.pin,proceso:ICONS.clock,hecho:ICONS.check,evidencia:ICONS.camera};
const $=s=>document.querySelector(s), autor=()=>document.querySelector('#autor').value||'anónimo';
let ROL='admin';
const LOADERS={resumen:cargarStats,seguimiento:cargarSeguimiento,mapa:cargarMapa,tendencias:cargarTendencias,escalados:cargarEscalados,redes:cargarRedes,explorar:cargarExplorar,contactos:cargarContactos,pruebas:cargarKB,alimentar:cargarPendientes,notas:cargarNotas,auditoria:cargarAuditoria,conexion:cargarConexion,usuarios:cargarUsuarios};
const TABS=Object.keys(LOADERS);
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  TABS.forEach(t=>$('#'+t).classList.toggle('hide',t!==b.dataset.t));
  try{localStorage.setItem('froy_tab',b.dataset.t);}catch(e){}
  (LOADERS[b.dataset.t]||function(){})();
});
function buscarSeccion(q){
  q=(q||'').trim().toLowerCase();
  if(!q)return;
  const btn=Array.from(document.querySelectorAll('.nav')).find(
    b=>b.style.display!=='none'&&b.textContent.toLowerCase().includes(q));
  if(btn)btn.click();
}
const TAGCLS={queja:'q',solicitud:'s',apoyo:'a'};
function tag(t){return '<span class="tag '+(TAGCLS[t]||'')+'">'+(t||'otro')+'</span>';}

async function cargarStats(){
  const s=await(await fetch('/api/stats')).json();
  $('#cards').innerHTML=`
   <div class="card stat featured"><div class="statbtn">${ICONS.arrowup}</div><div class="lbl">Conversaciones</div><div class="num">${s.total}</div><span class="pill up">${ICONS.up} ${s.municipios_alcanzados} municipios</span></div>
   <div class="card stat"><div class="statbtn">${ICONS.arrowup}</div><div class="lbl">Escalados</div><div class="num">${s.escalados}</div><span class="pill warn">${s.pct_escalados}% del total</span></div>
   <div class="card stat"><div class="statbtn">${ICONS.arrowup}</div><div class="lbl">Notas de mejora</div><div class="num">${s.notas}</div></div>
   <div class="card stat"><div class="statbtn">${ICONS.arrowup}</div><div class="lbl">Aportes pendientes</div><div class="num">${s.aportes_pendientes}</div></div>`;
  const max=Math.max(1,...s.por_tipo.map(x=>x[1]));
  $('#tipos').innerHTML=s.por_tipo.map(([k,v])=>`<div class="bar"><div class="nm">${k}</div><div class="track"><div class="fill" style="width:${100*v/max}%"></div></div><div class="vv">${v}</div></div>`).join('')||'<span class="muted">Sin datos aún</span>';
  $('#dpct').textContent=s.pct_escalados+'%';
  $('#donut').style.background=`conic-gradient(var(--amber) ${s.pct_escalados*3.6}deg, #eef0f6 0)`;
  $('#dtext').innerHTML=`<b>${s.escalados}</b> de <b>${s.total}</b> conversaciones requieren seguimiento del coordinador de zona.`;
  const geo=(arr)=>arr.map(([k,v])=>`<div class="geo"><span>${k}</span><b>${v}</b></div>`).join('')||'<span class="muted">Aún no se captura ubicación</span>';
  $('#munis').innerHTML=geo(s.top_municipios);$('#cols').innerHTML=geo(s.top_colonias);
  $('#recientes').innerHTML=s.recientes.map(r=>`<div class="geo"><span style="flex:1">${tag(r.tipo)} ${r.mensaje.slice(0,70)}</span><span class="muted">${[r.colonia,r.municipio].filter(Boolean).join(', ')||'—'} ${r.escalar?ICONS.flag:''}</span></div>`).join('')||'<span class="muted">Sin conversaciones aún. Prueba el chat en la pestaña Pruebas.</span>';
}

async function cargarTendencias(){
  const s=await(await fetch('/api/tendencias')).json();
  const max=Math.max(1,...s.serie.map(x=>x.total));
  $('#serie').innerHTML='<div class="vbars">'+s.serie.map(x=>`<div class="col"><div class="v">${x.total||''}</div><div class="bar2 ${x.total?'':'empty'}" style="height:${x.total?Math.max(24,Math.round(x.total/max*140)):40}px"></div><div class="d">${x.dia}</div></div>`).join('')+'</div>';
  const tmax=Math.max(1,...s.temas.map(x=>x[1]));
  $('#temasfreq').innerHTML=s.temas.length?s.temas.map(([k,v])=>`<div class="bar"><div class="nm" style="width:112px">${k}</div><div class="track"><div class="fill" style="width:${Math.round(v/tmax*100)}%"></div></div><div class="vv">${v}</div></div>`).join(''):'<span class="muted">Sin datos aún. Prueba el chat o conecta WhatsApp.</span>';
}

// --- mapa ---
let _map=null;
async function cargarMapa(){
  const d=await(await fetch('/api/mapa')).json();
  if(!_map){_map=L.map('mapa-canvas').setView([29.3,-110.7],6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:14,attribution:'© OpenStreetMap'}).addTo(_map);_map._mk=[];}
  _map._mk.forEach(m=>_map.removeLayer(m));_map._mk=[];
  const max=Math.max(1,...d.puntos.map(p=>p.total));
  d.puntos.forEach(p=>{const c=L.circleMarker([p.lat,p.lng],{radius:8+(p.total/max)*26,color:'#b5261e',fillColor:'#d8453b',fillOpacity:.55,weight:2}).addTo(_map);
    c.bindPopup('<b>'+p.municipio+'</b><br>'+p.total+' conversaciones');_map._mk.push(c);});
  setTimeout(()=>_map.invalidateSize(),150);
  $('#mapa-sin').innerHTML=d.puntos.length?(d.sin_coords.length?('Sin ubicar en el mapa: '+d.sin_coords.map(x=>x.municipio+' ('+x.total+')').join(', ')):''):'Aún no hay municipios capturados. Prueba el chat mencionando una ciudad.';
}
// --- escalados ---
async function cargarEscalados(){
  const it=await(await fetch('/api/escalados')).json();
  $('#esc-count').textContent=it.filter(x=>!x.atendido).length+' pendientes';
  $('#esc-list').innerHTML=it.length?it.map(x=>{
    const tel=(x.numero||'').replace(/[^0-9]/g,'');
    const wa=(tel&&x.numero!=='prueba')?`<a class="ghost" href="https://wa.me/${tel}" target="_blank" style="text-decoration:none">${ICONS.chat} Responder por WhatsApp</a>`:'';
    return `<div class="aporte ${x.atendido?'done':''}">
    <div class="muted">${x.fecha} · ${ICONS.phone} <b>${x.numero}</b> · ${[x.colonia,x.municipio].filter(Boolean).join(', ')||'sin ubicación'}${x.n>1?(' · '+x.n+' mensajes'):''}</div>
    <div style="margin:5px 0">${x.mensajes.map(m=>'• '+m).join('<br>')}</div>
    <div class="muted">tema: ${x.tema||'-'}</div>
    <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">${wa}
      <button class="ghost ${x.atendido?'':'ok'}" onclick="atender('${x.numero}')">${x.atendido?ICONS.undo+' reabrir':ICONS.check+' marcar atendido'}</button></div></div>`;
  }).join(''):'<span class="muted">Nada escalado todavía.</span>';
}
async function atender(numero){await fetch('/api/escalados/atender',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numero})});cargarEscalados();}
// --- explorar ---
async function cargarExplorar(){
  const q=$('#ex-q').value||'',tipo=$('#ex-tipo').value||'';
  const d=await(await fetch('/api/conversaciones?q='+encodeURIComponent(q)+'&tipo='+encodeURIComponent(tipo))).json();
  $('#ex-count').textContent=d.total+' resultados';
  $('#ex-list').innerHTML=d.items.length?d.items.map(x=>`<div class="conv">
    <div class="muted">${x.fecha} · ${tag(x.tipo)} ${[x.colonia,x.municipio].filter(Boolean).join(', ')} ${x.escalar?ICONS.flag:''} ${x.origen==='prueba'?'<span class="tag">prueba</span>':''}</div>
    <div>${ICONS.user} ${x.mensaje}</div><div>${ICONS.dot('#1a9d6c')} ${(x.respuesta||'').slice(0,220)}</div></div>`).join(''):'<span class="muted">Sin resultados</span>';
}
// --- contactos ---
const POSTURA_LBL={simpatizante:'Simpatizante',neutral:'Neutral',opositor:'Opositor',sin_datos:'Sin datos'};
async function cargarContactos(){
  const q=$('#ct-q').value||'', postura=$('#ct-postura').value||'';
  const d=await(await fetch('/api/contactos?q='+encodeURIComponent(q)+'&postura='+encodeURIComponent(postura))).json();
  $('#ct-count').textContent=d.total+' contacto'+(d.total===1?'':'s');
  $('#ct-body').innerHTML=d.items.length?d.items.map(x=>{
    const p=x.postura||'sin_datos';
    const fecha=(x.actualizado_en||'').slice(0,16).replace('T',' ');
    const tema=x.tema?('<span title="'+x.tema.replace(/"/g,'&quot;')+'">'+x.tema.slice(0,40)+(x.tema.length>40?'…':'')+'</span>'):'<span class="muted">—</span>';
    return `<tr>
      <td>${x.numero}</td>
      <td>${x.nombre||'<span class="muted">—</span>'}</td>
      <td>${x.ciudad||'<span class="muted">—</span>'}</td>
      <td>${x.colonia||'<span class="muted">—</span>'}</td>
      <td>${tema}</td>
      <td><span class="postura ${p}">${POSTURA_LBL[p]||p}</span></td>
      <td>${x.mensajes||0}</td>
      <td class="muted">${fecha}</td>
    </tr>`;
  }).join(''):'<tr><td colspan="8"><span class="muted">Todavía no hay contactos registrados.</span></td></tr>';
}
// --- auditoría ---
async function cargarAuditoria(){
  const it=await(await fetch('/api/auditoria')).json();
  $('#aud-list').innerHTML=it.length?it.map(x=>`<div class="nota">
    <div class="muted">${x.fecha}</div>
    <div>${ICONS.user} <i>${x.mensaje}</i></div>
    <div style="margin:4px 0;color:#c0392b">${ICONS.ban} ${(x.bloqueado||'').slice(0,220)}</div>
    <div class="muted">motivo: ${(x.motivos||[]).join(' · ')}</div></div>`).join(''):'<span class="muted">Sin bloqueos. El bot no ha tenido que frenar nada.</span>';
}
// --- usuarios ---
async function cargarUsuarios(){
  const us=await(await fetch('/api/usuarios')).json();
  $('#us-list').innerHTML=(Array.isArray(us)&&us.length)?us.map(u=>`<div class="geo"><span>${u.usuario} <span class="tag">${u.rol}</span></span><button class="ghost no" onclick="delUser('${u.usuario}')">Eliminar</button></div>`).join(''):'<span class="muted">Solo está el admin del sistema</span>';
}
async function addUser(){
  const u=$('#u-user').value.trim(),c=$('#u-pass').value.trim(),r=$('#u-rol').value;
  if(!u||!c){$('#u-flash').textContent='Pon usuario y contraseña';return;}
  const d=await(await fetch('/api/usuarios',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({usuario:u,clave:c,rol:r})})).json();
  $('#u-flash').textContent=d.ok?'✓ usuario creado':('⚠ '+(d.error||'error'));
  if(d.ok){$('#u-user').value='';$('#u-pass').value='';cargarUsuarios();}
}
async function delUser(u){if(confirm('¿Eliminar a '+u+'?')){await fetch('/api/usuarios/eliminar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({usuario:u})});cargarUsuarios();}}
// --- redes (war room digital) ---
const REDINFO={'Facebook':{c:'#1877F2',i:ICONS.facebook},'Instagram':{c:'#E1306C',i:ICONS.camera},'Otro':{c:'#8a93a6',i:ICONS.globe}};
const ACCINFO={'Comentar':ICONS.chat,'Dar like':ICONS.thumbup,'Reacción':ICONS.heart,'Compartir':ICONS.share};
const _ri=red=>REDINFO[red]||REDINFO['Otro'];
const puedeEditar=()=>ROL==='admin'||ROL==='coordinador';
function chipRed(red){const r=_ri(red);return `<span class="chip net" style="background:${r.c}">${r.i} ${red||'Red'}</span>`;}
function chipAcc(a){return `<span class="chip acc">${ACCINFO[a]||'•'} ${a||'Acción'}</span>`;}
function chipPerf(p){return p?`<span class="chip perf">${ICONS.user} ${p}</span>`:'';}
function chipEstado(e){const ok=(e==='hecho');return `<span class="chip st ${ok?'ok':''}">${ok?ICONS.check+' hecho':(e||'pendiente')}</span>`;}
function evGrid(ev){return (ev&&ev.length)?`<div class="evgrid">${ev.map(e=>`<a href="/evidencia/${e.archivo}" target="_blank" title="${e.autor||''} · ${e.fecha||''}"><img src="/evidencia/${e.archivo}"></a>`).join('')}</div>`:'';}
function tarjetaRed(x){
  const r=_ri(x.red), ev=x.evidencias||[];
  const com=(x.comentario||'').replace(/</g,'&lt;');
  const comBox=x.comentario?`<div class="combox">${ICONS.chat} ${com} <button class="btnmini" style="padding:2px 8px;font-size:11px" onclick='copiar(this,${JSON.stringify(x.comentario)})'>copiar</button></div>`:'';
  const edit=puedeEditar()?`<button class="btnmini" onclick="abrirEdit(${x.id})">${ICONS.edit} Editar</button>`:'';
  const del=puedeEditar()?`<button class="btnmini dgr" onclick="delAccion(${x.id})">${ICONS.trash} Eliminar</button>`:'';
  return `<div class="rcard"><div class="top" style="background:${r.c}"></div><div class="body">
    <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
      <div style="display:flex;gap:6px;flex-wrap:wrap">${chipRed(x.red)} ${chipAcc(x.accion)} ${chipPerf(x.perfil)}</div>
      ${chipEstado(x.estado)}
    </div>
    ${comBox}
    ${ev.length?`<div class="muted" style="font-size:12px;margin-top:9px">${ICONS.camera} ${ev.length} evidencia(s)</div>${evGrid(ev)}`:''}
    <div class="muted" style="font-size:11px;margin-top:9px">Creado por ${x.autor||'-'} · ${x.fecha||''}</div>
    <div class="rowbtns">
      <a class="btnmini pri" href="${x.link}" target="_blank" rel="noopener">${ICONS.link} Abrir post</a>
      <label class="btnmini" style="cursor:pointer">${ICONS.clip} Evidencia<input type="file" accept="image/*" hidden onchange="subirEvidencia(${x.id},this)"></label>
      <button class="btnmini ${x.estado==='hecho'?'':'ok'}" onclick="accEstado(${x.id})">${x.estado==='hecho'?ICONS.undo+' Reabrir':ICONS.check+' Hecho'}</button>
      ${edit}${del}
    </div></div></div>`;
}
async function cargarRedes(){
  const a=await(await fetch('/api/acciones')).json();
  $('#red-pend').textContent=a.filter(x=>x.estado!=='hecho').length+' pendientes';
  const card=$('#red-form-card'); if(card) card.style.display=puedeEditar()?'':'none';
  $('#red-list').innerHTML=a.length?a.map(tarjetaRed).join(''):'<span class="muted">Sin acciones aún. Crea la primera arriba.</span>';
}
async function abrirEdit(id){
  const a=await(await fetch('/api/acciones')).json();
  const x=a.find(v=>v.id===id); if(!x)return;
  $('#e-id').value=id;$('#e-red').value=x.red||'Facebook';$('#e-link').value=x.link||'';$('#e-accion').value=x.accion||'Comentar';$('#e-perfil').value=x.perfil||'';$('#e-com').value=x.comentario||'';
  $('#editmodal').classList.remove('hide');
}
function cerrarEdit(){$('#editmodal').classList.add('hide');}
async function guardarEdit(){
  const b={id:parseInt($('#e-id').value),red:$('#e-red').value,link:$('#e-link').value,accion:$('#e-accion').value,perfil:$('#e-perfil').value,comentario:$('#e-com').value};
  await fetch('/api/acciones/editar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
  cerrarEdit(); cargarRedes(); if($('#seguimiento')&&!$('#seguimiento').classList.contains('hide'))cargarSeguimiento();
}
async function addAccion(){
  const link=$('#r-link').value.trim();
  if(!link){$('#r-flash').textContent='Falta el link';return;}
  const d=await(await fetch('/api/acciones',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    red:$('#r-red').value,link:link,accion:$('#r-accion').value,perfil:$('#r-perfil').value,comentario:$('#r-com').value})})).json();
  $('#r-flash').textContent=d.ok?'✓ acción publicada':('⚠ '+(d.error||'error'));
  if(d.ok){$('#r-link').value='';$('#r-com').value='';cargarRedes();}
}
async function accEstado(id){await fetch('/api/acciones/estado',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});cargarRedes();}
async function subirEvidencia(id,input){
  const f=input.files[0]; if(!f)return;
  const fd=new FormData(); fd.append('id',id); fd.append('archivo',f);
  const prev=input.parentElement; prev.style.opacity=.5;
  const d=await(await fetch('/api/acciones/evidencia',{method:'POST',body:fd})).json();
  prev.style.opacity=1;
  if(d.ok) cargarRedes(); else alert(d.error||'error al subir');
}
async function delAccion(id){if(confirm('¿Eliminar esta acción?')){await fetch('/api/acciones/eliminar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});cargarRedes();}}
function copiar(btn,txt){navigator.clipboard.writeText(txt).then(()=>{const o=btn.textContent;btn.textContent='✓ copiado';setTimeout(()=>btn.textContent=o,1500);});}

// --- seguimiento (kanban) ---
const KORDER=['porhacer','proceso','hecho','evidencia'];
async function cargarSeguimiento(){
  const s=await(await fetch('/api/seguimiento')).json();
  const m=s.meta||{}, obj=m.objetivo||0, av=m.avance||0;
  const pct=obj>0?Math.min(100,Math.round(av/obj*100)):0;
  $('#seg-bar').style.width=pct+'%';
  $('#seg-metanum').innerHTML=(m.texto?('<b>'+m.texto+'</b> · '):'')+(obj>0?(av+' / '+obj+' ('+pct+'%)'):(av+' publicaciones con evidencia'));
  const mf=$('#seg-metaform'); if(mf) mf.style.display=(ROL==='admin'||ROL==='coordinador')?'':'none';
  const rankcolor=i=>['#c9a227','#9aa5b1','#b5651d'][i]||'#c7bcc0';
  $('#seg-persona').innerHTML=s.por_persona.length?s.por_persona.map(([n,c],i)=>`<div class="geo"><span><span class="inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold text-white align-[-5px]" style="background:${rankcolor(i)}">${i+1}</span> ${n}</span><b>${c}</b></div>`).join(''):'<span class="muted">Aún no hay evidencias subidas</span>';
  $('#kanban').innerHTML=s.columnas.map(c=>`<div class="kcol" data-col="${c.key}" ondragover="event.preventDefault();this.classList.add('over')" ondragleave="this.classList.remove('over')" ondrop="soltar(event,'${c.key}')">
    <h4><span>${COLICONS[c.key]||''} ${c.titulo}</span><span class="tag">${c.items.length}</span></h4>
    ${c.items.map(x=>ktarjeta(x,c.key)).join('')||'<div class="muted" style="font-size:12px">—</div>'}
  </div>`).join('');
}
function ktarjeta(x,col){
  const r=_ri(x.red), ev=(x.evidencias||[]).length, idx=KORDER.indexOf(col);
  const izq=idx>0?`<button class="btnmini" style="padding:3px 8px" onclick="moverCol(${x.id},'${KORDER[idx-1]}')">◀</button>`:'';
  const der=idx<KORDER.length-1?`<button class="btnmini" style="padding:3px 8px" onclick="moverCol(${x.id},'${KORDER[idx+1]}')">▶</button>`:'';
  const edit=puedeEditar()?`<button class="btnmini" style="padding:3px 8px" onclick="abrirEdit(${x.id})">${ICONS.edit}</button>`:'';
  return `<div class="kcard" draggable="true" ondragstart="event.dataTransfer.setData('id',${x.id})" style="border-left:4px solid ${r.c}">
    <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:5px">${chipRed(x.red)} ${chipAcc(x.accion)}</div>
    ${x.perfil?('<div style="font-size:12px;color:var(--grad1);font-weight:600">'+ICONS.user+' '+x.perfil+'</div>'):''}
    ${x.comentario?('<div style="font-size:12px;color:#666;margin-top:3px">'+ICONS.chat+' '+x.comentario.slice(0,60).replace(/</g,'&lt;')+(x.comentario.length>60?'…':'')+'</div>'):''}
    ${ev?('<div class="muted" style="font-size:11px;margin-top:5px">'+ICONS.camera+' '+ev+' evidencia(s)</div>'):''}
    <div style="margin-top:8px;display:flex;gap:5px;align-items:center;justify-content:space-between;flex-wrap:wrap">
      <a class="btnmini" style="padding:3px 9px" href="${x.link}" target="_blank" rel="noopener">${ICONS.link}</a>
      <span style="display:flex;gap:4px">${izq}${der}${edit}</span>
    </div></div>`;
}
async function moverCol(id,col){await fetch('/api/acciones/columna',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,columna:col})});cargarSeguimiento();}
async function soltar(e,col){e.preventDefault();document.querySelectorAll('.kcol').forEach(k=>k.classList.remove('over'));const id=parseInt(e.dataTransfer.getData('id'));if(!isNaN(id))await moverCol(id,col);}
async function guardarMeta(){await fetch('/api/meta',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texto:$('#seg-mtexto').value,objetivo:$('#seg-mobj').value})});cargarSeguimiento();}

// --- conexión Green API ---
async function cargarConexion(){
  $('#g-webhook').textContent=location.origin.replace('panel','bot').replace(/froy-[a-z0-9]+\./,'')+'/green-webhook';
  // el webhook va al dominio del BOT (webhook server), no al del panel:
  $('#g-webhook').textContent='https://187.127.251.161.sslip.io/green-webhook';
  const c=await(await fetch('/api/green-config')).json();
  $('#g-id').value=c.id_instance||''; $('#g-token').value=c.api_token||''; $('#g-url').value=c.api_url||'https://api.green-api.com';
  probarGreen(true);
}
async function guardarGreen(){
  const b={id_instance:$('#g-id').value,api_token:$('#g-token').value,api_url:$('#g-url').value};
  const d=await(await fetch('/api/green-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
  $('#g-flash').textContent=d.ok?'✓ Guardado':'⚠ error'; probarGreen(true);
}
async function probarGreen(silent){
  $('#g-estado').innerHTML='<span class="muted">Consultando…</span>';
  const e=await(await fetch('/api/green-estado')).json();
  let html, st=e.stateInstance;
  if(st==='authorized') html='<span class="chip st ok" style="font-size:13px">'+ICONS.dot('#1a9d6c')+' Conectado (authorized)</span>';
  else if(st) html='<span class="chip st" style="font-size:13px">'+ICONS.dot('#ffb020')+' Estado: '+st+' — escanea el QR en Green API</span>';
  else html='<span class="chip st" style="font-size:13px">'+ICONS.dot('#b8a4aa')+' '+(e.error||'sin configurar')+'</span>';
  $('#g-estado').innerHTML=html;
  if(!silent)$('#g-flash').textContent='';
}

// --- roles: muestra solo las pestañas permitidas ---
const TABS_ROL={admin:['resumen','seguimiento','mapa','tendencias','escalados','redes','explorar','contactos','pruebas','alimentar','notas','auditoria','conexion','usuarios'],
  coordinador:['resumen','seguimiento','mapa','tendencias','escalados','redes','explorar','contactos','pruebas','notas'],
  brigadista:['seguimiento','pruebas','redes','notas']};
async function initRol(){
  let me={usuario:'local',rol:'admin'};
  try{me=await(await fetch('/api/me')).json();}catch(e){}
  const rol=me.rol||'brigadista'; ROL=rol;
  $('#whoami').textContent=me.usuario+' ('+rol+')';
  $('#avatarletra').textContent=(me.usuario||'?')[0].toUpperCase();
  if($('#autor')&&!$('#autor').value)$('#autor').value=me.usuario||'';
  const permit=TABS_ROL[rol]||['pruebas'];
  document.querySelectorAll('.nav').forEach(b=>{if(!permit.includes(b.dataset.t))b.style.display='none';});
  // ocultar etiquetas de grupo que se quedaron sin botones visibles
  document.querySelectorAll('.navgrp').forEach(g=>{
    let vis=false,n=g.nextElementSibling;
    while(n&&!n.classList.contains('navgrp')){if(n.classList.contains('nav')&&n.style.display!=='none'){vis=true;break;}n=n.nextElementSibling;}
    g.style.display=vis?'':'none';
  });
  let guardada=null;
  try{guardada=localStorage.getItem('froy_tab');}catch(e){}
  const visibles=Array.from(document.querySelectorAll('.nav')).filter(b=>b.style.display!=='none');
  const recordar=guardada&&visibles.find(b=>b.dataset.t===guardada);
  const activar=recordar||visibles[0];
  if(activar)activar.click();
}

// chat
let hist=[];
function add(t,c){const d=document.createElement('div');d.className='b '+c;d.textContent=t;$('#chat').appendChild(d);$('#chat').scrollTop=1e9;return d;}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function enviar(){
  const i=$('#msg'),m=i.value.trim();if(!m)return;i.value='';add(m,'yo');hist.push({role:'user',content:m});
  const t=add('escribiendo…','bot');
  const d=await(await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mensaje:m,historial:hist})})).json();
  t.remove();if(d.error){add('⚠ '+d.error,'bot');return;}
  const sim=$('#sim').checked,gl=d.globos&&d.globos.length?d.globos:[d.respuesta];
  for(let k=0;k<gl.length;k++){if(sim){const tp=add('escribiendo…','bot');await sleep(Math.min(500+gl[k].length*40,3500));tp.remove();}add(gl[k],'bot');}
  const mt=document.createElement('div');mt.className='meta';mt.textContent='meta: '+tag2(d.meta);$('#chat').appendChild(mt);
  const ln=document.createElement('div');ln.className='lnk';ln.innerHTML=ICONS.bulb+' anotar mejora';ln.onclick=()=>anotar(ln,m,d.respuesta);$('#chat').appendChild(ln);
  hist.push({role:'assistant',content:d.respuesta});$('#chat').scrollTop=1e9;
}
function tag2(m){return['tipo:'+(m.tipo||'-'),'col:'+(m.colonia||'-'),'mun:'+(m.municipio||'-'),'escalar:'+(m.escalar||false)].join('  ·  ');}
function anotar(ln,msg,resp){
  if(ln.dataset.o)return;ln.dataset.o=1;
  const w=document.createElement('div');w.className='notaform';
  w.innerHTML=`
    <textarea class="nt" placeholder="¿Qué mejorarías de esta respuesta?"></textarea>
    <div class="row2">
      <select class="cat"><option value="tono">Tono/naturalidad</option><option value="dato">Dato incorrecto</option><option value="legal">Legal/compliance</option><option value="captura">Captura de datos</option><option value="otro" selected>Otro</option></select>
      <select class="pri"><option value="alta">Prioridad alta</option><option value="media" selected>Prioridad media</option><option value="baja">Prioridad baja</option></select>
    </div>
    <textarea class="ideal" placeholder="(Opcional) ¿Qué debería haber contestado el bot?"></textarea>
    <button class="ghost">Guardar mejora</button> <span class="flash"></span>`;
  ln.after(w);
  w.querySelector('button').onclick=async()=>{
    const n=w.querySelector('.nt').value.trim();if(!n)return;
    await fetch('/api/nota',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      autor:autor(),mensaje:msg,respuesta:resp,nota:n,
      categoria:w.querySelector('.cat').value,prioridad:w.querySelector('.pri').value,
      respuesta_ideal:w.querySelector('.ideal').value})});
    w.querySelector('.flash').textContent='✓ guardada';
    w.querySelectorAll('textarea,select,button').forEach(e=>e.disabled=true);};
}
async function cargarKB(){const f=await(await fetch('/api/conocimiento')).json();$('#kb').innerHTML=f.map(x=>ICONS.doc+' '+x).join('<br>');}

// alimentar
async function aprender(){
  const fd=new FormData();fd.append('autor',autor());fd.append('titulo',$('#t_titulo').value);fd.append('contenido',$('#t_cont').value);
  const f=$('#t_file').files[0];if(f)fd.append('archivo',f);
  const esAudio=f && /\.(mp3|m4a|ogg|opus|wav|aac|amr|aiff|flac|webm)$/i.test(f.name);
  $('#f_aprender').textContent=esAudio?'⏳ Transcribiendo el audio, puede tardar un poco…':'⏳ Enviando…';
  const d=await(await fetch('/api/aprender',{method:'POST',body:fd})).json();
  $('#f_aprender').textContent=d.ok?('✓ Aporte recibido. Pendiente de aprobar.'):('⚠ '+(d.error||'error'));
  if(d.ok){$('#t_titulo').value='';$('#t_cont').value='';$('#t_file').value='';$('#dropname').textContent='';cargarPendientes();}
}
(function(){
  const dz=$('#drop'),fi=$('#t_file');if(!dz)return;
  const nombre=()=>{$('#dropname').textContent=fi.files[0]?('📄 '+fi.files[0].name):'';};
  dz.onclick=()=>fi.click(); fi.onchange=nombre;
  ['dragenter','dragover'].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault();dz.classList.add('over');}));
  ['dragleave','dragend','drop'].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault();dz.classList.remove('over');}));
  dz.addEventListener('drop',ev=>{if(ev.dataTransfer.files.length){fi.files=ev.dataTransfer.files;nombre();}});
})();
async function cargarPendientes(){
  const a=await(await fetch('/api/aportes')).json();$('#npend').textContent=a.length;
  $('#pendientes').innerHTML=a.length?a.map(x=>`<div class="aporte"><b>${x.archivo}</b><pre>${x.preview.replace(/</g,'&lt;')}</pre>
    <div class="row" style="margin-top:8px"><button class="ghost ok" onclick="aprobar('${x.archivo}')">${ICONS.check} Aprobar e integrar</button><button class="ghost no" onclick="rechazar('${x.archivo}')">${ICONS.xmark} Rechazar</button></div></div>`).join(''):'<span class="muted">Nada pendiente por ahora.</span>';
}
async function aprobar(a){if(!confirm('¿Integrar este aporte a la base de conocimiento? El agente lo usará de inmediato.'))return;
  const d=await(await fetch('/api/aportes/aprobar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({archivo:a})})).json();
  cargarPendientes();}
async function rechazar(a){await fetch('/api/aportes/rechazar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({archivo:a})});cargarPendientes();}

// notas
const PRICOL={alta:'#ff5d5d',media:'#ffb020',baja:'#19c37d'};
function badgePri(p){const c=PRICOL[p]||'#888';return `<span class="tag" style="background:${c}22;color:${c}">${p||'media'}</span>`;}
async function cargarNotas(){
  const n=await(await fetch('/api/notas')).json();
  $('#listanotas').innerHTML=n.length?n.map(x=>`<div class="nota ${x.estado==='resuelta'?'done':''}">
    <div class="muted" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">${x.autor} · ${x.fecha} ${badgePri(x.prioridad)} <span class="tag">${x.categoria||'otro'}</span></div>
    <div style="margin:6px 0">“${x.nota}”</div>
    ${x.respuesta_ideal?`<div class="ideal-box">${ICONS.bulb} Ideal: ${x.respuesta_ideal}</div>`:''}
    <div class="muted">sobre: <i>${(x.mensaje||'').slice(0,70)}</i></div>
    <div class="row2" style="margin-top:8px;align-items:center"><span class="muted">estado:</span>
      <select onchange="setEstado(${x._id},this.value)">
        ${['pendiente','en progreso','resuelta'].map(e=>`<option ${x.estado===e?'selected':''}>${e}</option>`).join('')}
      </select></div></div>`).join(''):'<span class="muted">Sin notas aún</span>';
}
async function setEstado(id,estado){await fetch('/api/nota/estado',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,estado})});cargarNotas();}

$('#msg').addEventListener('keydown',e=>{if(e.key==='Enter')enviar();});
initRol();
</script></body></html>"""

if __name__ == "__main__":
    print("Dashboard en http://localhost:3000")
    app.run(host="0.0.0.0", port=3000, debug=False)
