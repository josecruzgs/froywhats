#!/usr/bin/env python3
"""
Reloj único del sistema: se guarda en UTC, se muestra en horario de Tijuana.

Antes cada módulo resolvía la hora por su cuenta: server.py e intervenciones.py con
`utcnow()` (UTC) y dashboard.py con `now()` (hora local del VPS). En el panel convivían
dos relojes distintos, ninguno era el de Tijuana, y nadie convertía nada al pintar: por
eso un mensaje de mediodía aparecía con la hora de UTC. Aquí queda una sola regla:

    GUARDAR  ->  iso()   UTC con offset explícito   2026-09-07T19:04:12+00:00
    MOSTRAR  ->  fmt()   convertido a Tijuana       2026-09-07 12:04

Guardar en UTC (y no en hora local) mantiene el orden alfabético de las cadenas ISO
igual al orden cronológico, que es de lo que dependen los `sort` del panel; con offsets
que cambian en verano eso dejaría de cumplirse.

Los registros escritos antes de este cambio no traen offset. `parse()` los asume UTC,
que es lo que eran salvo el puñado de notas/casos/pruebas que escribió el panel con la
hora del VPS.
"""
import os, datetime

UTC = datetime.timezone.utc
TZ_NOMBRE = os.environ.get("FROY_TZ", "America/Tijuana")


class _PacificoFallback(datetime.tzinfo):
    """Plan B para cuando el sistema no trae la base de zonas horarias (Windows sin el
    paquete `tzdata`). Tijuana sigue las reglas de EE. UU.: UTC-8 en invierno y UTC-7
    del segundo domingo de marzo al primer domingo de noviembre."""
    _STD = datetime.timedelta(hours=-8)
    _DST = datetime.timedelta(hours=-7)
    _UNA_HORA = datetime.timedelta(hours=1)

    @staticmethod
    def _domingo(anio, mes, n):
        """El n-ésimo domingo de ese mes, a las 2 de la mañana (hora del cambio)."""
        d = datetime.datetime(anio, mes, 1)
        return (d + datetime.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))).replace(hour=2)

    def _en_verano(self, dt):
        if dt is None:
            return False
        d = dt.replace(tzinfo=None)
        return self._domingo(d.year, 3, 2) <= d < self._domingo(d.year, 11, 1)

    def utcoffset(self, dt):
        return self._DST if self._en_verano(dt) else self._STD

    def dst(self, dt):
        return self._UNA_HORA if self._en_verano(dt) else datetime.timedelta(0)

    def tzname(self, dt):
        return "PDT" if self._en_verano(dt) else "PST"


def _cargar_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NOMBRE)
    except Exception as e:     # sin tzdata instalado: se usan las reglas a mano
        print("Zona horaria " + TZ_NOMBRE + " no disponible (" + str(e) +
              "), uso las reglas del Pacífico a mano", flush=True)
        return _PacificoFallback()


TZ = _cargar_tz()


def ahora():
    """El instante actual, siempre consciente de su zona (UTC). Reemplaza a `utcnow()`,
    que devolvía un datetime ingenuo y está obsoleto desde Python 3.12."""
    return datetime.datetime.now(UTC)


def iso(dt=None, timespec="seconds"):
    """Cómo se GUARDA una fecha: UTC con offset explícito, para que nunca vuelva a haber
    duda sobre qué reloj la escribió."""
    return (dt or ahora()).astimezone(UTC).isoformat(timespec=timespec)


def parse(valor):
    """Lee una fecha guardada y la devuelve consciente de su zona. Lo escrito antes de
    este cambio viene sin offset: se asume UTC."""
    if isinstance(valor, datetime.datetime):
        dt = valor
    else:
        try:
            dt = datetime.datetime.fromisoformat((valor or "").strip())
        except (TypeError, ValueError, AttributeError):
            return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def local(valor):
    """El mismo instante, visto desde Tijuana."""
    dt = parse(valor)
    return dt.astimezone(TZ) if dt else None


def fmt(valor, formato="%Y-%m-%d %H:%M"):
    """Cómo se MUESTRA: hora de Tijuana, lista para pintar en el panel. Devuelve '' si la
    fecha no se entiende, que es lo que el panel ya sabía manejar."""
    dt = local(valor)
    return dt.strftime(formato) if dt else ""


def dia(valor):
    """Día natural en Tijuana ('2026-09-07'), para agrupar sin que un mensaje de la tarde
    se contabilice en el día siguiente."""
    dt = local(valor)
    return dt.strftime("%Y-%m-%d") if dt else ""


def hoy():
    """La fecha de hoy en Tijuana, no la del reloj del servidor."""
    return ahora().astimezone(TZ).date()


def sello(formato="%Y%m%d-%H%M%S"):
    """Marca de tiempo para nombres de archivo: en hora local, que es la que va a leer
    quien abra la carpeta."""
    return ahora().astimezone(TZ).strftime(formato)
