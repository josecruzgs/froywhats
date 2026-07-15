#!/usr/bin/env python3
"""Demo en consola del ritmo humano: reproduce lectura, 'escribiendo…' y globos con sus tiempos.

Simula lo que verá el ciudadano en WhatsApp, pero en la terminal y en tiempo real.
Uso: python tests/demo_humano.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import agente, humanizar

MENSAJES = [
    "oye y quien es froylan? no lo conozco",
    "hola buenas! vamos con todo froy desde la colonia centenario",
    "tengo 4 dias sin agua, nadie viene",
    "esto ya es para gobernador verdad?",
]

def barra(seg, etiqueta):
    """Muestra una cuenta regresiva sencilla mientras 'pasa' el tiempo."""
    fin = time.time() + seg
    while time.time() < fin:
        restante = fin - time.time()
        print(f"\r   …{etiqueta} ({restante:0.1f}s)   ", end="", flush=True)
        time.sleep(min(0.1, max(0, restante)))
    print("\r" + " " * 40 + "\r", end="", flush=True)

def main():
    for msg in MENSAJES:
        print("\n" + "=" * 60)
        print(f"👤 Ciudadano: {msg}")
        t0 = time.time()
        salida = agente.responder(msg)
        plan = humanizar.plan_de_envio(msg, salida.get("respuesta", ""))
        print("   ✓✓ (leído)")
        total = 0
        for paso in plan:
            total += paso["seg"]
            if paso["accion"] == "leer":
                barra(paso["seg"], "leyendo")
            elif paso["accion"] == "pausa":
                barra(paso["seg"], "")
            elif paso["accion"] == "escribir":
                barra(paso["seg"], "escribiendo")
                print(f"🟢 Froy-bot: {paso['texto']}")
        n_globos = sum(1 for p in plan if p["accion"] == "escribir")
        print(f"   [⏱ respuesta en {n_globos} globo(s), ritmo humano ~{total:0.1f}s "
              f"+ {time.time()-t0-total:0.1f}s de 'pensar']")

if __name__ == "__main__":
    main()
