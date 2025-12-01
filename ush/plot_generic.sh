#!/bin/bash
set -euo pipefail

# Minimal generic wrapper: calls a matching python core if it exists,
# otherwise errors with guidance.

# Forward all args to python if a core exists at ush/<var>_from_grib.py
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
  python3 "${CORE}" "$@" --out "${OUTPNG}"
  echo "Wrote ${OUTPNG}"
  exit 0
fi

echo "No python core found for VAR=${VAR} (expected $(basename "${CORE}"))." >&2
echo "Create ${CORE} (or a dedicated plot_${VAR_LC}.sh) to implement plotting." >&2
exit 6

