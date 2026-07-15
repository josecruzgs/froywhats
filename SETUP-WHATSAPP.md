# Guía: conectar el bot a WhatsApp (Meta Cloud API)

Esta parte la haces tú en las plataformas de Meta (requiere el login del equipo). Al final
copias 3 datos al archivo `.env` y el servidor ya queda conectado.

## Antes de empezar — checklist
- [ ] Acceso al **portafolio de negocios** de Meta donde vive la página verificada
      (business.facebook.com).
- [ ] Un **número de teléfono nuevo**, que NO esté usado en ningún WhatsApp ni WhatsApp Business
      (app del celular). Puede ser un chip nuevo o un número virtual que reciba SMS/llamada.

## Paso 1 — Crear la App
1. Entra a https://developers.facebook.com/apps
2. **Crear app** → tipo **Negocios (Business)**.
3. Vincúlala al **mismo portafolio de negocios** de la página verificada.

## Paso 2 — Agregar WhatsApp
1. Dentro de la app: **Agregar producto → WhatsApp → Configurar**.
2. Meta crea automáticamente una **cuenta de WhatsApp Business (WABA)** y un número de prueba.
3. En **WhatsApp → Configuración de la API** verás:
   - **Phone Number ID** (ID del número)  → va en `.env`
   - **WhatsApp Business Account ID** (ID de la WABA)

## Paso 3 — Registrar el número real del bot
1. En **WhatsApp → Configuración de la API → Agregar número de teléfono**.
2. Escribe el número nuevo y verifícalo por SMS o llamada.
3. Ese número será el que vea la gente. (El de prueba solo sirve para tests internos.)

## Paso 4 — Token permanente (System User)
El token temporal dura 24 h. Para producción:
1. **Configuración del negocio → Usuarios → Usuarios del sistema → Agregar**.
2. Crea un usuario del sistema tipo **Administrador**.
3. **Asignar activos** → asigna la **App** y la **WABA** con control total.
4. **Generar token** con los permisos:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
5. Copia ese token (es largo). → va en `.env` como `WHATSAPP_TOKEN`.

## Paso 5 — Configurar el webhook
El webhook es la URL pública a donde Meta manda los mensajes entrantes.
1. Necesitas el servidor corriendo con **HTTPS público** (ver `src/server.py` y la nota de ngrok abajo).
2. En **WhatsApp → Configuración → Webhooks → Editar**:
   - **Callback URL:** `https://TU-DOMINIO/webhook`
   - **Verify token:** `froy-verify-2026` (el mismo que está en `.env`)
3. **Verificar y guardar.** Si el servidor está bien, Meta lo acepta.
4. **Suscríbete al campo `messages`** (botón "Administrar" → activar `messages`).

## Paso 6 — Verificación del negocio (para salir a producción)
- En **Configuración del negocio → Centro de seguridad → Verificar negocio.**
- Sin esto, el número está limitado a pocos destinatarios de prueba.
- Con la figura pública ya verificada, este paso es más rápido.

## Los 3 datos que van al .env
```
WHATSAPP_TOKEN=EAAG...            (Paso 4)
WHATSAPP_PHONE_NUMBER_ID=1234...  (Paso 2)
WHATSAPP_VERIFY_TOKEN=froy-verify-2026   (ya está)
```

## Probar el webhook en local (mientras no hay servidor en la nube)
WhatsApp necesita una URL HTTPS pública. Para pruebas, un túnel:
```
# en otra terminal, con el servidor corriendo en el puerto 8000:
npx localtunnel --port 8000     # o:  cloudflared tunnel --url http://localhost:8000
```
Usa la URL https que te dé como Callback URL del Paso 5.
```
