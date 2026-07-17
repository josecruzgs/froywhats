# Despliegue en VPS (Ubuntu 24.04)

Deja el bot de WhatsApp y el dashboard corriendo 24/7 con HTTPS. Tiempo estimado: ~30 min.

Reemplaza `tudominio.com` por tu dominio real en todos los pasos.

---

## 1. DNS — apuntar el dominio al VPS
En tu proveedor de dominio, crea dos registros **A** apuntando a la **IP del VPS**:
```
@        A    <IP_DEL_VPS>      (para tudominio.com  -> webhook)
panel    A    <IP_DEL_VPS>      (para panel.tudominio.com -> dashboard)
```
Espera unos minutos a que propague.

## 2. Entrar al VPS y preparar el sistema
```bash
ssh root@<IP_DEL_VPS>
apt update && apt upgrade -y
apt install -y python3-venv python3-pip git ufw
# Firewall: solo SSH + web
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 3. Subir el proyecto a /opt/froy
Opción A (desde tu Mac, con scp):
```bash
# en tu Mac, dentro de la carpeta del proyecto:
rsync -av --exclude .venv --exclude videos --exclude transcripts \
      ./ root@<IP_DEL_VPS>:/opt/froy/
```
Opción B: si lo tienes en GitHub, `git clone` en `/opt/froy`.

## 4. Entorno e instalación de dependencias
```bash
cd /opt/froy
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r deploy/requirements.txt
```

## 5. Crear el archivo .env (secretos)
```bash
nano /opt/froy/.env
```
Pega esto y rellena tus valores:
```
ANTHROPIC_API_KEY=sk-ant-...
MODELO_BORRADOR=claude-sonnet-4-6
MODELO_CRITICO=claude-sonnet-4-6
MAX_LOOPS=2

WHATSAPP_TOKEN=EAAG...
WHATSAPP_PHONE_NUMBER_ID=123456...
WHATSAPP_VERIFY_TOKEN=froy-verify-2026

PANEL_USER=admin
PANEL_PASS=PonUnaContraseñaFuerteAquí
```
Protege el archivo: `chmod 600 /opt/froy/.env`
Permisos para el usuario del servicio: `chown -R www-data:www-data /opt/froy`

## 6. Servicios systemd (se reinician solos)
```bash
cp /opt/froy/deploy/froy-webhook.service   /etc/systemd/system/
cp /opt/froy/deploy/froy-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now froy-webhook froy-dashboard
systemctl status froy-webhook --no-pager     # debe decir "active (running)"
```

## 7. Caddy (HTTPS automático)
```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

# coloca tu Caddyfile (edítalo antes con tu dominio real)
cp /opt/froy/deploy/Caddyfile /etc/caddy/Caddyfile
nano /etc/caddy/Caddyfile        # cambia tudominio.com
systemctl reload caddy
```
Caddy saca el certificado HTTPS solo. Prueba en el navegador:
- `https://panel.tudominio.com`  -> te pide usuario/contraseña (los del .env) -> dashboard.
- `https://tudominio.com/`        -> "Agente de Froy activo 🟢".

## 8. Conectar el webhook en Meta
En **developers.facebook.com → tu App → WhatsApp → Configuración → Webhooks**:
- **Callback URL:** `https://tudominio.com/webhook`
- **Verify token:** el mismo de `WHATSAPP_VERIFY_TOKEN`
- Verifica y **suscríbete al campo `messages`**.

¡Listo! Manda un WhatsApp al número del bot y debe contestar.

---

## Comandos útiles
```bash
# Ver logs en vivo
journalctl -u froy-webhook -f
journalctl -u froy-dashboard -f

# Reiniciar tras un cambio de código o de la base de conocimiento
systemctl restart froy-webhook froy-dashboard

# Actualizar el proyecto (tras subir cambios a git)
cd /opt/froy && git pull && systemctl restart froy-webhook froy-dashboard
```

## Notas
- La transcripción de videos NO va en el VPS (es solo de la Mac).
- `data/registros.jsonl` y `data/notas_mejora.jsonl` viven en el VPS; respáldalos de vez en cuando.
- Para que el dashboard refleje aportes aprobados al instante ya recarga solo; si editas archivos
  de `knowledge-base/` a mano, reinicia los servicios.
