#!/bin/bash
set -euo pipefail

ORIG_ARGS=("$@")   # <-- ADD THIS LINE (save all args)

# Minimal generic wrapper...
VAR=""; COMOUT=""; FHR=""; VDATE=""; VHH=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --var) VAR="$2"; shift 2;;
    --comout) COMOUT="$2"; shift 2;;
    --fhr) FHR="$2"; shift 2;;
    --vdate) VDATE="$2"; shift 2;;
    --vhour) VHH="$2"; shift 2;;
    *) shift;;
  esac
done

VAR_LC=$(echo "${VAR}" | tr '[:upper:]' '[:lower:]')
CORE="$(dirname "$0")/${VAR_LC}_from_grib.py"

if [[ -f "${CORE}" ]]; then
  OUTPNG="${COMOUT}/${VAR_LC}.f$(printf "%03d" ${FHR}).${VDATE}${VHH}.png"
  python3 "${CORE}" "${ORIG_ARGS[@]}" --out "${OUTPNG}"   # <-- CHANGE "$@" to ORIG_ARGS
  echo "Wrote ${OUTPNG}"
  exit 0
fi

