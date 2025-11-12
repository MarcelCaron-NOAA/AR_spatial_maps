#!/bin/bash
set -euo pipefail

# Thin wrapper to call Python plotter with standardized args
ARGS=("$@")

# Parse minimal args to craft output name
MODEL=""; FILE=""; FHR=""; VDATE=""; VHH=""
DOMAIN=""; COMOUT=""; HOME_DIR=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --model) MODEL="$2"; shift 2;;
    --file) FILE="$2"; shift 2;;
    --fhr) FHR="$2"; shift 2;;
    --vdate) VDATE="$2"; shift 2;;
    --vhour) VHH="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    --comout) COMOUT="$2"; shift 2;;
    --home) HOME_DIR="$2"; shift 2;;
    *) shift;;
  esac
done

mkdir -p "${COMOUT}"

if [[ "${machine}" == "gaeac6" ]]; then
    "${CONDA_PREFIX}/bin/python" "$(dirname "$0")/ivt_from_grib.py" "${ARGS[@]}"
else
    python3 "$(dirname "$0")/ivt_from_grib.py" "${ARGS[@]}"
fi
