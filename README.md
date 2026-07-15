# Agente Virtual de WhatsApp — Froylán Gámez

Asistente de WhatsApp que atiende a ciudadanos de Sonora durante el proceso interno de Morena,
responde con la información oficial de campaña y registra datos georreferenciados (municipio/colonia)
para segmentación territorial.

## Arquitectura
```
WhatsApp (ciudadano)
   ⇅  webhook
Meta WhatsApp Cloud API (oficial)
   ⇅
Servidor Node.js
   ⇅
Claude API + Base de Conocimiento (RAG)
   ⇄
Base de datos (registro de peticiones + georreferencia / CRM)
```

## Estructura
```
Froylan/
├─ knowledge-base/            ← El "entrenamiento" del bot (RAG)
│  ├─ 01-quien-es-froylan.md
│  ├─ 02-logros-4t-sonora.md
│  ├─ 03-respuestas-tipo-calle.md
│  ├─ 04-reglas-compliance.md   ← Blindaje legal (máxima prioridad)
│  ├─ 05-persona-tono.md
│  └─ 06-audiencias.md
├─ src/
│  └─ prompt/
│     └─ system-prompt.md     ← Cerebro: instrucciones + captura de datos
└─ README.md
```

## Estado actual
- [x] Base de conocimiento estructurada (7 archivos: manual narrativo + reglas anti-bot).
- [x] System prompt con persona, reglas de cumplimiento y captura de datos.
- [x] Núcleo del agente con **loop de refinamiento** (borrador → crítica → refina): `src/agente.py`.
- [x] Batería de 12 pruebas con trampas legales: `tests/` (probado contra la API, funciona).
- [x] Servidor + webhook de WhatsApp Cloud API: `src/server.py` (probado end-to-end).
- [x] Registro georreferenciado por conversación: `data/registros.jsonl` (municipio/colonia/tema).
- [x] Guía de conexión a Meta: `SETUP-WHATSAPP.md`.
- [ ] Anexo pendiente: **Fichas_Proyectos_SEC 5.05.26 (ANEXO CARMÍN)** (logros SEC).
- [ ] Conectar credenciales reales de Meta (lo hace el equipo; ver `SETUP-WHATSAPP.md`).
- [ ] Migrar `registros.jsonl` a una base de datos real cuando crezca el volumen.

## Ritmo humano (que no parezca bot)
`src/humanizar.py` modela lectura, escritura por globos y pausas; el servidor muestra
"escribiendo…" y palomita azul. Ajustable por `.env` (HUMANO_CPS, HUMANO_MAX_GLOBOS…).
Demo en consola: `python tests/demo_humano.py`.

## Dashboard de administración
```bash
source .venv/bin/activate
python src/dashboard.py     # abre http://localhost:8080
```
Estética bento-grid / soft-UI con 4 áreas:
- **📊 Resumen** — estadísticas y segmentación geográfica (de `data/registros.jsonl`).
- **🧪 Pruebas** — chatear con el agente y anotar mejoras.
- **📚 Alimentar** — subir info (texto **o audios**, con arrastrar-y-soltar) y **aprobar/rechazar**
  aportes. Los **audios se transcriben solos** (faster-whisper en el VPS). Aprobar integra el aporte
  a `knowledge-base/` y recarga el agente al instante (aprobados → `data/aportes_aprobados/`).
- **💡 Notas** — mejoras sugeridas por el equipo (`data/notas_mejora.jsonl`).

(`src/pruebas_web.py` fue la versión simple previa; el dashboard la reemplaza.)

## Servidor de WhatsApp
```bash
source .venv/bin/activate
python src/server.py      # webhook en http://localhost:8000/webhook
```
Para conectar el número real de WhatsApp, sigue `SETUP-WHATSAPP.md` y llena las 3 variables
de WhatsApp en `.env`.

## Cómo probar el agente (local)
```bash
source .venv/bin/activate
python tests/correr_pruebas.py      # corre los 12 casos
python src/agente.py                # chat interactivo en consola
```

## Despliegue en producción (VPS)
Guía completa en `DEPLOY-VPS.md`. El kit está en `deploy/` (requirements, Caddyfile con HTTPS
automático, servicios systemd). El webhook procesa en segundo plano (responde a Meta al instante
y deduplica reintentos) y el dashboard va protegido con contraseña (`PANEL_USER`/`PANEL_PASS`).

## Lo que se necesita de Meta (WhatsApp Cloud API)
1. Cuenta de **Meta Business** verificada.
2. App en **developers.facebook.com** con el producto *WhatsApp* añadido.
3. Un **número de teléfono** dedicado para el bot.
4. **Phone Number ID** y **WhatsApp Business Account ID**.
5. **Token de acceso** (permanente, vía System User).
6. Un **Verify Token** (lo definimos nosotros) para el webhook.
7. Servidor con **HTTPS público** para recibir el webhook.
```
