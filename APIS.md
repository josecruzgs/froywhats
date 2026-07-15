# APIs y arquitectura — Agente Froy

Documentación de todos los endpoints del sistema: el **bot** (webhooks) y el **dashboard** (panel de administración).

## Arquitectura

```
WhatsApp
  ├─ Meta Cloud API  ─┐
  └─ Green API (QR)  ─┤→  Servidor bot (Flask/gunicorn, puerto 8000)
                       │      └─ Claude API + base de conocimiento (RAG) + loop de refinamiento
                       │      └─ Registro georreferenciado → data/registros.jsonl
Dashboard admin ───────┘   Servidor dashboard (Flask/gunicorn, puerto 8080)
```

Ambos servicios corren con **gunicorn + systemd** detrás de **Caddy** (HTTPS automático).

---

## 1. Servidor del bot (`src/server.py`, puerto 8000)

Recibe mensajes de WhatsApp y responde con el agente. Procesa en segundo plano (responde 200 al instante) y aplica el ritmo humano (globos con pausas).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Estado de salud ("Agente de Froy activo 🟢") |
| GET | `/webhook` | Verificación del webhook de **Meta** (responde `hub.challenge` si el `hub.verify_token` coincide con `WHATSAPP_VERIFY_TOKEN`) |
| POST | `/webhook` | Mensajes entrantes de **Meta Cloud API**. Deduplica por `message_id`, procesa en hilo, responde vía Graph API |
| POST | `/green-webhook` | Mensajes entrantes de **Green API**. Deduplica por `idMessage`, procesa en hilo, responde vía Green API |

**URLs públicas:** `https://187.127.251.161.sslip.io/webhook` y `.../green-webhook`

---

## 2. Dashboard (`src/dashboard.py`, puerto 8080)

Autenticación **HTTP Basic** en todas las rutas (usuario/rol vía `PANEL_USER`/`PANEL_PASS` o `data/usuarios.json`). Roles: `admin`, `coordinador`, `brigadista`.

### Sesión y estadísticas
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| GET | `/` | cualquiera | Panel HTML (Cache-Control: no-store) |
| GET | `/api/me` | cualquiera | Usuario y rol de la sesión |
| GET | `/api/stats` | cualquiera | Totales, por tipo, top municipios/colonias, escalados |
| GET | `/api/tendencias` | cualquiera | Conversaciones por día (14 días) + temas frecuentes |
| GET | `/api/mapa` | cualquiera | Puntos georreferenciados por municipio (Leaflet) |

### Pruebas y conocimiento
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| POST | `/api/chat` | cualquiera | Probar el agente (devuelve respuesta + globos + meta) |
| GET | `/api/conocimiento` | cualquiera | Lista de archivos de la base de conocimiento |
| POST | `/api/aprender` | cualquiera | Subir texto/audio (audio se transcribe) → aporte pendiente |
| GET | `/api/aportes` | cualquiera | Aportes pendientes de aprobar |
| POST | `/api/aportes/aprobar` | admin | Integra el aporte a la base y recarga el agente |
| POST | `/api/aportes/rechazar` | admin | Descarta el aporte |

### Operación ciudadana
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| GET | `/api/escalados` | cualquiera | Mensajes escalados, agrupados por conversación |
| POST | `/api/escalados/atender` | cualquiera | Marca/desmarca una conversación como atendida |
| GET | `/api/conversaciones` | cualquiera | Buscar/filtrar conversaciones (`?q=&tipo=`) |
| GET | `/export.csv` | cualquiera | Exporta todas las conversaciones a CSV |
| GET | `/api/auditoria` | cualquiera | Bloqueos de cumplimiento del agente |

### Redes y seguimiento (Kanban)
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| GET | `/api/acciones` | cualquiera | Publicaciones/acciones en redes |
| POST | `/api/acciones` | admin/coord | Crear acción (red, link, acción, perfil, comentario) |
| POST | `/api/acciones/editar` | admin/coord | Editar una acción |
| POST | `/api/acciones/eliminar` | admin/coord | Eliminar una acción |
| POST | `/api/acciones/estado` | cualquiera | Alternar hecho/pendiente |
| POST | `/api/acciones/columna` | cualquiera | Mover en el Kanban (porhacer/proceso/hecho/evidencia) |
| POST | `/api/acciones/evidencia` | cualquiera | Subir imagen de evidencia (marca como hecho) |
| GET | `/evidencia/<archivo>` | cualquiera | Ver una evidencia |
| GET | `/api/seguimiento` | cualquiera | Tablero Kanban + actividad por persona + meta |
| GET/POST | `/api/meta` | admin/coord (POST) | Meta y avance |

### Notas, usuarios y conexión
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| POST/GET | `/api/nota` · `/api/notas` | cualquiera | Notas de mejora |
| POST | `/api/nota/estado` | cualquiera | Cambiar estado de una nota |
| GET/POST | `/api/usuarios` | admin | Listar/crear usuarios |
| POST | `/api/usuarios/eliminar` | admin | Eliminar usuario |
| GET/POST | `/api/green-config` | admin | Credenciales de Green API |
| GET | `/api/green-estado` | admin | Estado de la instancia de Green API |

---

## 3. Green API (WhatsApp por QR) — `src/green_api.py`

WhatsApp no oficial. Se configura desde el dashboard (pestaña **⚙️ Conexión**) o por variables de entorno.

- **Enviar:** `POST {apiUrl}/waInstance{idInstance}/sendMessage/{apiToken}` → `{chatId, message}`
- **Estado:** `GET {apiUrl}/waInstance{idInstance}/getStateInstance/{apiToken}`
- **Recibir:** Green API hace POST a `https://187.127.251.161.sslip.io/green-webhook`
- Config en `data/green_config.json` (idInstance, apiToken, apiUrl).

---

## 4. Variables de entorno (`.env`)

```
ANTHROPIC_API_KEY=sk-ant-...
MODELO_BORRADOR=claude-sonnet-4-6
MODELO_CRITICO=claude-sonnet-4-6
MAX_LOOPS=2

# WhatsApp oficial (Meta) — opcional
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=froy-verify-2026

# Green API — opcional (mejor configúralo desde el dashboard)
GREEN_ID_INSTANCE=
GREEN_API_TOKEN=
GREEN_API_URL=https://api.green-api.com

# Dashboard
PANEL_USER=admin
PANEL_PASS=...

# Ritmo humano
HUMANIZAR=1
HUMANO_CPS=20
HUMANO_MAX_GLOBOS=3
```

> ⚠️ El `.env` y `data/` **no se suben a git** (ver `.gitignore`). Contienen secretos y datos.
