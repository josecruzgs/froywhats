#!/usr/bin/env python3
"""
Modela el ritmo humano de respuesta en WhatsApp: lectura, escritura por globos y pausas.

Basado en datos de comportamiento real:
- Tecleo en celular ~36-40 palabras/min (~3-4 chars/seg). Para UX usamos un "tecleo rápido"
  con tope, para que se sienta humano sin hacer esperar de más.
- Las personas leen antes de responder (mensajes largos -> más pausa).
- En WhatsApp se mandan varios mensajes cortos (globos) en vez de uno largo.

Todo es configurable por variables de entorno (ver abajo) y tiene aleatoriedad para que
NO siempre responda igual.
"""
import os, re, random

# --- Parámetros (ajustables por .env) ---
CPS          = float(os.environ.get("HUMANO_CPS", "20"))       # chars/seg al "escribir"
TOPE_GLOBO   = float(os.environ.get("HUMANO_TOPE_GLOBO", "6.5"))  # seg máx por globo
MIN_GLOBO    = float(os.environ.get("HUMANO_MIN_GLOBO", "1.0"))   # seg mín por globo
MAX_GLOBOS   = int(os.environ.get("HUMANO_MAX_GLOBOS", "3"))    # globos máx por respuesta
TOPE_LECTURA = float(os.environ.get("HUMANO_TOPE_LECTURA", "2.5"))

def dividir_en_globos(texto, max_globos=MAX_GLOBOS):
    """Parte la respuesta en globos naturales. Respeta los párrafos del modelo
    (separados por línea en blanco); si hay más que el tope, junta los últimos."""
    texto = (texto or "").strip()
    partes = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    if not partes:
        partes = [texto] if texto else []
    if len(partes) > max_globos:
        cabeza = partes[:max_globos - 1]
        cola = " ".join(partes[max_globos - 1:])
        partes = cabeza + [cola]
    return partes

def tiempo_lectura(texto_entrante):
    """Segundos que 'tarda en leer' el mensaje del ciudadano antes de empezar a escribir."""
    base = 0.6 + len(texto_entrante or "") / 120.0
    return round(min(base, TOPE_LECTURA) * random.uniform(0.85, 1.15), 2)

def tiempo_escritura(globo):
    """Segundos que 'tarda en escribir' un globo, según su largo + variación humana."""
    base = len(globo) / CPS
    jitter = random.uniform(0.85, 1.25)
    return round(max(MIN_GLOBO, min(base * jitter, TOPE_GLOBO)), 2)

def pausa_entre_globos():
    """Pausa entre un globo y el siguiente (varios segundos: nunca dos mensajes de golpe)."""
    return round(random.uniform(1.5, 3.2), 2)

def plan_de_envio(texto_entrante, respuesta):
    """Devuelve el 'guion' de tiempos para una respuesta, listo para reproducir.

    [{'accion': 'leer', 'seg': x},
     {'accion': 'escribir', 'seg': y, 'texto': globo},
     {'accion': 'pausa', 'seg': z}, ...]
    """
    plan = [{"accion": "leer", "seg": tiempo_lectura(texto_entrante)}]
    globos = dividir_en_globos(respuesta)
    for i, g in enumerate(globos):
        plan.append({"accion": "escribir", "seg": tiempo_escritura(g), "texto": g})
        if i < len(globos) - 1:
            plan.append({"accion": "pausa", "seg": pausa_entre_globos()})
    return plan
