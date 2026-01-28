#!/bin/bash
set -euo pipefail

ARGS=("$@")

# optional: pull out COMOUT so we can mkdir it
COMOUT=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --comout) COMOUT="$2"; shift 2;;
    *) shift;;
  esac
done

[[ -n "${COMOUT}" ]] && mkdir -p "${COMOUT}"

# Call python (respect machine like you already do)
if [[ "${machine}" == "gaeac6" ]]; then
  "${CONDA_PREFIX}/bin/python" "$(dirname "$0")/timeheight_from_grib.py" "${ARGS[@]}"
else
  python3 "$(dirname "$0")/timeheight_from_grib.py" "${ARGS[@]}"
fi

