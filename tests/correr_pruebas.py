#!/usr/bin/env python3
"""Corre todos los casos de tests/casos.json por el agente y muestra el resultado.

Uso:  python tests/correr_pruebas.py
Requiere ANTHROPIC_API_KEY. Cada caso muestra la respuesta final, los metadatos
capturados y cuántos loops de refinamiento necesitó.
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import agente

BASE = os.path.dirname(os.path.abspath(__file__))
casos = json.load(open(os.path.join(BASE, "casos.json")))["casos"]

print(f"Corriendo {len(casos)} casos...\n" + "=" * 70)
for c in casos:
    print(f"\n[{c['id']}]  ({c['tipo']})")
    print(f"  Ciudadano: {c['mensaje']}")
    if c.get("trampa"):
        print(f"  ⚠ Trampa: {c['trampa']}")
    out = agente.responder(c["mensaje"], verbose=True)
    print(f"  Froy-bot : {out.get('respuesta','')}")
    meta = out.get("meta", {})
    print(f"  meta     : tipo={meta.get('tipo')} colonia={meta.get('colonia')} "
          f"municipio={meta.get('municipio')} escalar={meta.get('escalar')}")
print("\n" + "=" * 70 + "\nListo.")
