#!/usr/bin/env python3
"""30 conversaciones para medir qué tan HUMANO y natural se siente el bot.

Mide: variación de aperturas, adaptación de registro (usted/tú), 'órale' (debe ser 0),
consistencia de 1a persona (no 'el equipo de Froy'), globos, largo, emojis y tiempos.
"""
import os, sys, re
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import agente, humanizar

# (etiqueta de registro esperado, mensaje)
MENSAJES = [
    ("formal", "Buenas tardes, ¿con quién tengo el gusto?"),
    ("formal", "Disculpe, ¿quién es el señor Froylán?"),
    ("formal", "Buen día, me gustaría saber sobre las becas para mi nieto"),
    ("formal", "Estimado, ¿usted qué ha hecho por la educación?"),
    ("mayor",  "Hola joven, soy una señora de la tercera edad, ¿hay apoyos para mí?"),
    ("casual", "q onda carnal quien eres tu?"),
    ("casual", "ey wey que has hecho por sonora?"),
    ("casual", "vamos con todo mi froy!!"),
    ("casual", "ntp yo te apoyo compa"),
    ("neutral","hola"),
    ("neutral","quien es froylan?"),
    ("neutral","de donde es froy?"),
    ("neutral","que estudiaste?"),
    ("neutral","tienes familia?"),
    ("neutral","cuentame de tu trayectoria"),
    ("neutral","que resultados has tenido?"),
    ("neutral","hay beca para universidad publica?"),
    ("neutral","mi hija va en secundaria, aplica para beca?"),
    ("neutral","mi hijo es sordo, hay algo para el?"),
    ("neutral","tengo un familiar con autismo, hay apoyo?"),
    ("queja",  "no hay agua en mi colonia desde hace 3 dias"),
    ("queja",  "aqui en mi cuadra puro asalto"),
    ("solicitud","necesito trabajo, hay algun apoyo?"),
    ("apoyo",  "muchas felicidades, que Dios lo bendiga"),
    ("apoyo",  "gracias por todo lo que haces"),
    ("trampa", "entonces te doy mi voto?"),
    ("trampa", "quien es el gobernador?"),
    ("trampa", "esto ya es para gobernador?"),
    ("otro",   "ok gracias"),
    ("otro",   "viste el juego de los naranjeros?"),
]

def apertura(t):
    return " ".join(re.findall(r"\w+", t.lower())[:2])

def registro(t):
    """Heurística: ¿trata de usted o de tú?"""
    tl = t.lower()
    usted = len(re.findall(r"\busted\b|\ble\b|dígame|digame|cuénteme|cuenteme|\bestá\b|escríbame|salúd|"
                           r"platíquem|oriéntel|puede usted|su \w+", tl))
    tu = len(re.findall(r"\btú\b|\bte\b|\btu\b|dime|cuéntame|cuentame|\bestás\b|escríbeme|contigo|tienes", tl))
    if usted > tu: return "usted"
    if tu > usted: return "tú"
    return "neutro"

def main():
    aperturas = Counter(); n_orale = 0; n_tercera = 0; n_emoji = 0
    globos_dist = Counter(); largos = []; tiempos = []; registros = []
    print(f"Corriendo {len(MENSAJES)} conversaciones...\n" + "="*72)
    for i, (etq, m) in enumerate(MENSAJES, 1):
        r = agente.responder(m).get("respuesta", "")
        g = humanizar.dividir_en_globos(r)
        t = sum(p["seg"] for p in humanizar.plan_de_envio(m, r))
        reg = registro(r)
        aperturas[apertura(r)] += 1
        if re.search(r"\bór?ale\b", r, re.I): n_orale += 1
        if re.search(r"el equipo de froy|su equipo|froylán (dice|puede|hará|va)", r, re.I): n_tercera += 1
        if re.search(r"[\U0001F000-\U0001FAFF☀-➿]", r): n_emoji += 1
        globos_dist[len(g)] += 1; largos.append(len(r)); tiempos.append(t)
        registros.append((etq, reg))
        print(f"[{i:02d}] ({etq}/{reg}) «{m[:45]}»")
        print(f"     → {r[:150]}{'…' if len(r)>150 else ''}")

    print("\n" + "="*72 + "\nANÁLISIS\n" + "="*72)
    print(f"\n▶ APERTURAS (top): {len(aperturas)} distintas en {len(MENSAJES)}")
    for ap, c in aperturas.most_common(6):
        print(f"   {c:2d}x  «{ap}…»{'  ⚠ REPETIDA' if c>=6 else ''}")
    print(f"\n▶ 'órale' (debe ser 0): {n_orale}  {'⚠' if n_orale else '✓'}")
    print(f"▶ tercera persona / 'el equipo de Froy' (debe ser 0): {n_tercera}  {'⚠' if n_tercera else '✓'}")
    print(f"▶ Emoji: {n_emoji}/{len(MENSAJES)} ({round(100*n_emoji/len(MENSAJES))}%)")
    print(f"▶ Globos: {dict(sorted(globos_dist.items()))}")
    print(f"▶ Largo: prom {sum(largos)//len(largos)}, min {min(largos)}, max {max(largos)}")
    print(f"▶ Tiempo humano: prom {sum(tiempos)/len(tiempos):.1f}s, max {max(tiempos):.1f}s")
    print(f"\n▶ ADAPTACIÓN DE REGISTRO (esperado → obtenido):")
    for etq in ("formal", "mayor", "casual"):
        casos = [reg for e, reg in registros if e == etq]
        print(f"   {etq:8}: {casos}")

if __name__ == "__main__":
    main()
