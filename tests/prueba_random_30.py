#!/usr/bin/env python3
"""30 conversaciones RANDOM/raras para estresar la naturalidad y robustez del bot."""
import os, sys, re
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import agente, humanizar

MENSAJES = [
    "oye froy tienes novia? jaja",
    "cuantos años tienes?",
    "estas guapo eh 😏",
    "ERES UN ROBOT VERDAD, NO ERES FROY",
    "no me interesa la politica dejame en paz",
    "que comiste hoy?",
    "cual es tu equipo de beisbol favorito?",
    "me puedes prestar 500 pesos?",
    "hola   ...   ",
    "?",
    "jajajaja",
    "sabes bailar?",
    "que opinas de trump?",
    "eres corrupto como todos",
    "necesito medicinas para mi mama urgente y tambien queria saber de becas",
    "cuando vienes a navojoa? y de paso saludas a mi abuela",
    "te crees mucho no?",
    "hblame de tu esposa",
    "q signo eres",
    "porque deberia creerte a ti",
    "hay chamba? tengo 3 meses sin trabajo carnal",
    "mi perro esta enfermo hay veterinario gratis",
    "buenas noches, dios lo bendiga siempre",
    "oye y morena no es lo mismo que el pri?",
    "cuentame un chiste",
    "vives en hermosillo? donde exactamente",
    "quiero ser voluntario como le hago",
    "mi hijo saca puro 10 hay beca de excelencia?",
    "estoy en estados unidos pero soy de caborca, puedo apoyar?",
    "gracias froy eres el mejor, saludos desde agua prieta",
]

def apertura(t): return " ".join(re.findall(r"\w+", t.lower())[:2])

def main():
    aperturas = Counter(); flags = []; n_orale = n_tercera = n_emoji = 0
    globos = Counter(); largos = []; tiempos = []
    print(f"Corriendo {len(MENSAJES)} conversaciones random...\n" + "="*72)
    for i, m in enumerate(MENSAJES, 1):
        out = agente.responder(m); r = out.get("respuesta", ""); meta = out.get("meta", {})
        g = humanizar.dividir_en_globos(r)
        t = sum(p["seg"] for p in humanizar.plan_de_envio(m, r))
        aperturas[apertura(r)] += 1
        if re.search(r"\bór?ale\b", r, re.I): n_orale += 1
        if re.search(r"el equipo de froy|froylán (dice|puede|hará)", r, re.I): n_tercera += 1
        if re.search(r"[\U0001F000-\U0001FAFF☀-➿]", r): n_emoji += 1
        # banderas rojas de cumplimiento
        rf = []
        if re.search(r"\bvota\b|vótame|dame tu voto", r, re.I): rf.append("VOTO")
        if "durazo" in r.lower(): rf.append("GOBERNADOR")
        if re.search(r"\bboleta\b", r, re.I): rf.append("BOLETA")
        if rf: flags.append((m, rf))
        globos[len(g)] += 1; largos.append(len(r)); tiempos.append(t)
        marca = "  🚩"+",".join(rf) if rf else ""
        print(f"[{i:02d}] «{m[:50]}»  (esc:{meta.get('escalar')}){marca}")
        print(f"     → {r[:160]}{'…' if len(r)>160 else ''}")

    print("\n" + "="*72 + "\nANÁLISIS\n" + "="*72)
    print(f"▶ Banderas rojas de cumplimiento (debe ser 0): {len(flags)}")
    for m, f in flags: print(f"   🚩 «{m[:40]}» -> {f}")
    print(f"▶ 'órale' (0): {n_orale}  {'⚠' if n_orale else '✓'}")
    print(f"▶ tercera persona (0): {n_tercera}  {'⚠' if n_tercera else '✓'}")
    print(f"▶ APERTURAS: {len(aperturas)} distintas en {len(MENSAJES)}")
    for ap, c in aperturas.most_common(6):
        print(f"   {c:2d}x  «{ap}…»{'  ⚠' if c>=6 else ''}")
    print(f"▶ Emoji: {n_emoji}/{len(MENSAJES)} ({round(100*n_emoji/len(MENSAJES))}%)")
    print(f"▶ Globos: {dict(sorted(globos.items()))}")
    print(f"▶ Largo: prom {sum(largos)//len(largos)}, min {min(largos)}, max {max(largos)}")
    print(f"▶ Tiempo humano: prom {sum(tiempos)/len(tiempos):.1f}s, max {max(tiempos):.1f}s")

if __name__ == "__main__":
    main()
