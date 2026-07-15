#!/usr/bin/env python3
"""
Área de pruebas web del agente de Froy.

Para que el equipo que prueba el sistema pueda:
  1. Chatear con el agente (ver cómo responde, en globos).
  2. Anotar mejoras sobre cualquier respuesta  -> data/notas_mejora.jsonl
  3. Subir información para que el agente aprenda -> data/aportes/ (queda PENDIENTE de revisión).

Correr:  python src/pruebas_web.py   (abre http://localhost:8080)
"""
import os, sys, json, re, datetime
from flask import Flask, request, jsonify, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agente, humanizar

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
APORTES = os.path.join(DATA, "aportes")
NOTAS = os.path.join(DATA, "notas_mejora.jsonl")
os.makedirs(APORTES, exist_ok=True)

app = Flask(__name__)

def _slug(t):
    t = re.sub(r"[^\w\s-]", "", (t or "").lower()).strip()
    return re.sub(r"[\s]+", "-", t)[:40] or "aporte"

@app.get("/")
def home():
    return Response(PAGINA, mimetype="text/html")

@app.post("/chat")
def chat():
    d = request.get_json(force=True)
    mensaje = (d.get("mensaje") or "").strip()
    historial = d.get("historial") or []
    if not mensaje:
        return jsonify({"error": "mensaje vacío"}), 400
    salida = agente.responder(mensaje, historial)
    respuesta = salida.get("respuesta", "")
    return jsonify({
        "respuesta": respuesta,
        "globos": humanizar.dividir_en_globos(respuesta),
        "meta": salida.get("meta", {}),
    })

@app.post("/nota")
def nota():
    d = request.get_json(force=True)
    registro = {
        "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
        "autor": (d.get("autor") or "anónimo").strip(),
        "mensaje": d.get("mensaje", ""),
        "respuesta": d.get("respuesta", ""),
        "nota": (d.get("nota") or "").strip(),
    }
    if not registro["nota"]:
        return jsonify({"error": "nota vacía"}), 400
    with open(NOTAS, "a") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})

@app.post("/aprender")
def aprender():
    autor = (request.form.get("autor") or "anónimo").strip()
    titulo = (request.form.get("titulo") or "").strip()
    contenido = (request.form.get("contenido") or "").strip()
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        contenido = (contenido + "\n\n" + archivo.read().decode("utf-8", "ignore")).strip()
        titulo = titulo or os.path.splitext(archivo.filename)[0]
    if not contenido:
        return jsonify({"error": "no hay contenido"}), 400
    sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    nombre = f"{sello}-{_slug(titulo)}.md"
    ruta = os.path.join(APORTES, nombre)
    with open(ruta, "w") as f:
        f.write(f"# {titulo or 'Aporte sin título'}\n\n"
                f"<!-- Subido por: {autor} el {sello}. PENDIENTE de revisión antes de integrar. -->\n\n"
                f"{contenido}\n")
    return jsonify({"ok": True, "archivo": nombre})

@app.get("/tablero")
def tablero():
    notas = []
    if os.path.exists(NOTAS):
        notas = [json.loads(l) for l in open(NOTAS) if l.strip()]
    aportes = sorted(os.listdir(APORTES))
    return jsonify({"notas": notas[::-1], "aportes": aportes[::-1]})


PAGINA = r"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Área de pruebas — Agente de Froy</title>
<style>
  :root{--verde:#075E54;--verde2:#25D366;--bg:#ECE5DD;--mio:#DCF8C6}
  *{box-sizing:border-box;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  body{margin:0;background:#f0f2f5;color:#111}
  header{background:var(--verde);color:#fff;padding:12px 18px;font-weight:600;font-size:18px}
  .wrap{display:flex;gap:16px;max-width:1100px;margin:16px auto;padding:0 12px;align-items:flex-start;flex-wrap:wrap}
  .col{flex:1;min-width:320px}
  .card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.1);overflow:hidden}
  .chat{height:62vh;overflow-y:auto;padding:14px;background:var(--bg)}
  .b{max-width:80%;padding:8px 11px;border-radius:10px;margin:6px 0;line-height:1.35;white-space:pre-wrap;font-size:14px;position:relative}
  .bot{background:#fff;border-top-left-radius:2px}
  .yo{background:var(--mio);margin-left:auto;border-top-right-radius:2px}
  .meta{font-size:11px;color:#667;margin:2px 0 10px 2px}
  .nota-link{font-size:11px;color:#0a7;cursor:pointer;display:inline-block;margin:0 0 8px 2px}
  .barra{display:flex;gap:8px;padding:10px;background:#f6f6f6;border-top:1px solid #eee}
  .barra input{flex:1;padding:10px;border:1px solid #ccc;border-radius:20px;font-size:14px}
  button{background:var(--verde2);color:#fff;border:0;border-radius:20px;padding:10px 16px;font-weight:600;cursor:pointer}
  button.alt{background:#eee;color:#333}
  h3{margin:0;padding:12px 14px;background:#fafafa;border-bottom:1px solid #eee;font-size:14px}
  .pad{padding:14px}
  .pad input,.pad textarea{width:100%;padding:9px;border:1px solid #ccc;border-radius:8px;margin:6px 0;font-size:14px;font-family:inherit}
  textarea{min-height:90px;resize:vertical}
  .ok{color:#0a7;font-size:13px;margin-top:6px;min-height:18px}
  .typing{font-style:italic;color:#888;font-size:13px}
  .small{font-size:12px;color:#777}
  label.row{display:flex;align-items:center;gap:6px;font-size:13px;color:#444;margin:4px 0}
  .nota-box{margin:0 0 10px 2px}.nota-box textarea{min-height:54px;width:90%}
</style></head><body>
<header>🟢 Área de pruebas — Agente de Froy <span class="small" style="float:right;font-weight:400">tu nombre: <input id="autor" placeholder="quién prueba" style="border:0;border-bottom:1px solid #fff7;background:transparent;color:#fff;width:120px"></span></header>
<div class="wrap">
  <div class="col">
    <div class="card">
      <h3>💬 Conversación de prueba</h3>
      <div class="chat" id="chat"></div>
      <div class="barra">
        <input id="msg" placeholder="Escribe como si fueras un ciudadano…" autocomplete="off">
        <button onclick="enviar()">Enviar</button>
      </div>
      <label class="row" style="padding:6px 12px"><input type="checkbox" id="simular"> simular tiempos humanos (escribiendo… y globos)</label>
    </div>
  </div>
  <div class="col">
    <div class="card" style="margin-bottom:16px">
      <h3>📚 Enseñarle algo al agente</h3>
      <div class="pad">
        <p class="small">Sube información (un dato, una respuesta, un documento). Queda <b>pendiente de revisión</b> antes de que el agente lo use.</p>
        <input id="t_titulo" placeholder="Título (ej. Logros en salud)">
        <textarea id="t_cont" placeholder="Pega aquí la información…"></textarea>
        <input type="file" id="t_file" accept=".txt,.md">
        <button onclick="aprender()">Enviar aporte</button>
        <div class="ok" id="ok_aprender"></div>
      </div>
    </div>
    <div class="card">
      <h3>📋 Lo que se ha registrado</h3>
      <div class="pad" id="tablero"><span class="small">Cargando…</span></div>
    </div>
  </div>
</div>
<script>
let hist=[];
const chat=document.getElementById('chat');
const autor=()=>document.getElementById('autor').value||'anónimo';
function add(txt,cls){const d=document.createElement('div');d.className='b '+cls;d.textContent=txt;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
function meta(m){const d=document.createElement('div');d.className='meta';d.textContent='🗂 '+['tipo:'+(m.tipo||'-'),'col:'+(m.colonia||'-'),'mun:'+(m.municipio||'-'),'escalar:'+(m.escalar||false)].join('  ·  ');chat.appendChild(d);}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

async function enviar(){
  const inp=document.getElementById('msg');const m=inp.value.trim();if(!m)return;
  inp.value='';add(m,'yo');hist.push({role:'user',content:m});
  const t=add('escribiendo…','bot typing');
  const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mensaje:m,historial:hist})});
  const data=await r.json();t.remove();
  if(data.error){add('⚠ '+data.error,'bot');return;}
  const sim=document.getElementById('simular').checked;
  const globos=data.globos&&data.globos.length?data.globos:[data.respuesta];
  for(let i=0;i<globos.length;i++){
    if(sim){const tp=add('escribiendo…','bot typing');await sleep(Math.min(600+globos[i].length*45,4000));tp.remove();}
    const b=add(globos[i],'bot');
    addNota(b,m,data.respuesta);
    if(sim&&i<globos.length-1)await sleep(500);
  }
  meta(data.meta||{});
  hist.push({role:'assistant',content:data.respuesta});
  cargarTablero();
}
function addNota(bubble,msg,resp){
  const link=document.createElement('div');link.className='nota-link';link.textContent='💡 anotar mejora';
  link.onclick=()=>{
    if(bubble.dataset.open)return;bubble.dataset.open=1;
    const box=document.createElement('div');box.className='nota-box';
    box.innerHTML='<textarea placeholder="¿Qué mejorarías de esta respuesta?"></textarea><br><button class="alt">Guardar mejora</button> <span class="ok"></span>';
    link.after(box);
    box.querySelector('button').onclick=async()=>{
      const nota=box.querySelector('textarea').value.trim();if(!nota)return;
      await fetch('/nota',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({autor:autor(),mensaje:msg,respuesta:resp,nota:nota})});
      box.querySelector('.ok').textContent='✓ guardada';box.querySelector('textarea').disabled=true;cargarTablero();
    };
  };
  link.after(document.createElement('br'));chat.insertBefore(link,null);chat.scrollTop=chat.scrollHeight;
}
async function aprender(){
  const fd=new FormData();
  fd.append('autor',autor());
  fd.append('titulo',document.getElementById('t_titulo').value);
  fd.append('contenido',document.getElementById('t_cont').value);
  const f=document.getElementById('t_file').files[0];if(f)fd.append('archivo',f);
  const r=await fetch('/aprender',{method:'POST',body:fd});const d=await r.json();
  const ok=document.getElementById('ok_aprender');
  if(d.ok){ok.textContent='✓ Aporte recibido ('+d.archivo+'). Pendiente de revisión.';document.getElementById('t_titulo').value='';document.getElementById('t_cont').value='';document.getElementById('t_file').value='';cargarTablero();}
  else ok.textContent='⚠ '+(d.error||'error');
}
async function cargarTablero(){
  const d=await(await fetch('/tablero')).json();
  let h='';
  h+='<b>💡 Mejoras anotadas ('+d.notas.length+')</b>';
  if(!d.notas.length)h+='<div class="small">— ninguna aún —</div>';
  d.notas.slice(0,6).forEach(n=>{h+='<div style="margin:6px 0;border-left:3px solid #25D366;padding-left:8px"><div class="small">'+n.autor+' · '+n.fecha+'</div><div style="font-size:13px">“'+n.nota+'”</div></div>';});
  h+='<hr style="border:0;border-top:1px solid #eee;margin:12px 0"><b>📚 Aportes subidos ('+d.aportes.length+')</b>';
  if(!d.aportes.length)h+='<div class="small">— ninguno aún —</div>';
  d.aportes.slice(0,8).forEach(a=>{h+='<div class="small" style="margin:3px 0">📄 '+a+'</div>';});
  document.getElementById('tablero').innerHTML=h;
}
document.getElementById('msg').addEventListener('keydown',e=>{if(e.key==='Enter')enviar();});
cargarTablero();
</script></body></html>"""

if __name__ == "__main__":
    print("Área de pruebas en http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
