#!/usr/bin/env bash
# Limpia los datos operativos de Froy para arrancar en limpio con mensajes reales.
#
# BORRA: conversaciones, historial, contactos, registros georreferenciados,
#        auditoría, notas de mejora, escalados atendidos, acciones digitales,
#        seguimiento de metas, aportes y evidencias.
# CONSERVA: green_config.json (credenciales Green API), usuarios.json (logins del
#           panel), .env y la knowledge-base.
#
# Uso:
#   ./deploy/limpiar-datos.sh              # muestra qué borraría (dry-run)
#   ./deploy/limpiar-datos.sh --si         # borra de verdad (hace backup antes)
#   ./deploy/limpiar-datos.sh --si --usuarios   # además borra usuarios.json
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$BASE/data"

CONFIRMAR=0
BORRAR_USUARIOS=0
for arg in "$@"; do
  case "$arg" in
    --si|--yes|-y) CONFIRMAR=1 ;;
    --usuarios)    BORRAR_USUARIOS=1 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

ARCHIVOS=(
  "conversaciones.db" "conversaciones.db-wal" "conversaciones.db-shm"
  "registros.jsonl" "auditoria.jsonl" "notas_mejora.jsonl"
  "escalados_atendidos.json" "acciones_digitales.json" "meta_seguimiento.json"
)
[ "$BORRAR_USUARIOS" = 1 ] && ARCHIVOS+=("usuarios.json")

CARPETAS=("aportes" "aportes_aprobados" "aportes_rechazados" "evidencias")

if [ ! -d "$DATA" ]; then
  echo "No existe $DATA — nada que limpiar."
  exit 0
fi

echo "Datos en: $DATA"
echo
echo "Se borrarían estos archivos:"
for f in "${ARCHIVOS[@]}"; do
  [ -e "$DATA/$f" ] && echo "  - $f ($(du -h "$DATA/$f" | cut -f1))"
done
echo "Se vaciarían estas carpetas:"
for d in "${CARPETAS[@]}"; do
  [ -d "$DATA/$d" ] && echo "  - $d/ ($(find "$DATA/$d" -type f | wc -l) archivos)"
done
echo
echo "Se conservan: green_config.json, .env, knowledge-base/$([ "$BORRAR_USUARIOS" = 1 ] || echo ", usuarios.json")"

if [ "$CONFIRMAR" != 1 ]; then
  echo
  echo "DRY-RUN. Para borrar de verdad:  $0 --si"
  exit 0
fi

# Detener servicios para que nadie escriba a mitad del borrado.
SERVICIOS=()
for s in froy-webhook froy-dashboard; do
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$s" 2>/dev/null; then
    SERVICIOS+=("$s")
  fi
done
if [ ${#SERVICIOS[@]} -gt 0 ]; then
  echo "Deteniendo: ${SERVICIOS[*]}"
  systemctl stop "${SERVICIOS[@]}"
fi

# Backup antes de tocar nada.
BACKUP="$BASE/backup-datos-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$BACKUP" -C "$BASE" data
echo "Backup guardado en: $BACKUP"

for f in "${ARCHIVOS[@]}"; do
  rm -f "$DATA/$f"
done
for d in "${CARPETAS[@]}"; do
  mkdir -p "$DATA/$d"
  find "$DATA/$d" -mindepth 1 -delete
done

echo "Datos limpios."

if [ ${#SERVICIOS[@]} -gt 0 ]; then
  echo "Levantando: ${SERVICIOS[*]}"
  systemctl start "${SERVICIOS[@]}"
  systemctl --no-pager --lines=0 status "${SERVICIOS[@]}" || true
fi
