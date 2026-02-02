#!/bin/bash
set -euo pipefail

# repo driver: source env + config, resolve file path, call plotter
THISDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPDIR="$(cd "${THISDIR}/.." && pwd)"

# 1) Environment
if [[ "${HOSTNAME}" == "gaea6"* ]] || [[ "${HOSTNAME}" == "c6"* ]]; then
   export machine=gaeac6
elif [[ "${HOSTNAME}" == "clogin"* ]] || [[ "${HOSTNAME}" == "dlogin"* ]] || [[ "${HOSTNAME}" == "nid"* ]]; then
   export machine=wcoss2
else
   echo "Machine not supported: ${HOSTNAME}" >&2; exit 2
fi
source "${TOPDIR}/modulefiles/${machine}.env"

# 2) Config
CFG="${1:-${TOPDIR}/parm/config.example}"
if [[ ! -f "$CFG" ]]; then
  echo "Config not found: $CFG" >&2; exit 2
fi
# shellcheck disable=SC1090
source "$CFG"

mkdir -p "${COMOUT}" "${TMP}"

TEMPLATE_SFC_RAW=""
SFC_FILE_PATH=""

# 3) Time logic
# Seed both pairs from the provided DATE/HOUR (from config)
IDATE="$DATE"; IHOUR="$HOUR"
VDATE="$DATE"; VHOUR="$HOUR"

if [[ "${DATE_TYPE}" == "VALID" ]]; then
  # Given valid → derive init = valid - FHR
  VALID_STR="${VDATE}${VHOUR}"
  VALID_EPOCH=$(date -u -d "${VALID_STR:0:8} ${VALID_STR:8:2} UTC" +%s)
  INIT_EPOCH=$(( VALID_EPOCH - FHR*3600 ))
  IDATE=$(date -u -d "@${INIT_EPOCH}" +%Y%m%d)
  IHOUR=$(date -u -d "@${INIT_EPOCH}" +%H)
elif [[ "${DATE_TYPE}" == "INIT" ]]; then
  # Given init → derive valid = init + FHR
  INIT_STR="${IDATE}${IHOUR}"
  INIT_EPOCH=$(date -u -d "${INIT_STR:0:8} ${INIT_STR:8:2} UTC" +%s)
  VALID_EPOCH=$(( INIT_EPOCH + FHR*3600 ))
  VDATE=$(date -u -d "@${VALID_EPOCH}" +%Y%m%d)
  VHOUR=$(date -u -d "@${VALID_EPOCH}" +%H)
else
  echo "ERROR: DATE_TYPE must be INIT or VALID (got '${DATE_TYPE}')." >&2
  exit 2
fi

echo "MODEL=${MODEL} FHR=${FHR}"
echo "INIT:  ${IDATE} ${IHOUR}Z"
echo "VALID: ${VDATE} ${VHOUR}Z"

MET_MODELS="${MET_MODELS:-$MODEL}"
FHR3=$(printf "%03d" "${FHR}")

VAR_LC=$(echo "${VAR}" | tr '[:upper:]' '[:lower:]')

# --- Build FILE_TEMPLATE (time-heights need it; safe for all vars) ---
TEMPLATE_SFC_RAW=""
case "${MODEL}" in
  gfsv16) TEMPLATE_RAW="${TEMPLATE_GFSV16}";;
  gfsctl) TEMPLATE_RAW="${TEMPLATE_GFSCTL}";;
  gfsdeny) TEMPLATE_RAW="${TEMPLATE_GFSDENY}";;
  gfsv17) TEMPLATE_RAW="${TEMPLATE_GFSV17}";;
  aigfsv1) 
      TEMPLATE_RAW="${TEMPLATE_AIGFSV1_PRES}"
      TEMPLATE_SFC_RAW="${TEMPLATE_AIGFSV1_SFC}"
      ;;
  gdas)   TEMPLATE_RAW="${TEMPLATE_GDAS}";;
  urma)   TEMPLATE_RAW="${TEMPLATE_URMA}";;
  rap)   TEMPLATE_RAW="${TEMPLATE_RAP}";;
  arafs)  TEMPLATE_RAW="${TEMPLATE_ARAFS}";;
  *) echo "Unsupported MODEL=${MODEL}" >&2; exit 3;;
esac

# Expand everything except {FHR3}; leave {FHR3} for later substitution
FILE_TEMPLATE="${TEMPLATE_RAW//\{IDATE\}/$IDATE}"
FILE_TEMPLATE="${FILE_TEMPLATE//\{IHOUR\}/$IHOUR}"

case "${MODEL}" in
  gfsv16) FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_GFSV16\}/$HEAD_GFSV16}" ;;
  gfsctl) FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_GFSCTL\}/$HEAD_GFSCTL}" ;;
  gfsdeny) FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_GFSDENY\}/$HEAD_GFSDENY}" ;;
  gfsv17) FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_GFSV17\}/$HEAD_GFSV17}" ;;
  gdas)   FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_GDAS\}/$HEAD_GDAS}" ;;
  urma)   FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_URMA\}/$HEAD_URMA}" ;;
  rap)   FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_RAP\}/$HEAD_RAP}" ;;
  arafs)  FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_ARAFS\}/$HEAD_ARAFS}" ;;
  aigfsv1)FILE_TEMPLATE="${FILE_TEMPLATE//\{HEAD_AIGFSV1\}/$HEAD_AIGFSV1}" ;;
  *) echo "Unsupported MODEL=${MODEL}" >&2; exit 3 ;;
esac

if [[ "${VAR_LC}" != "timeheight" ]]; then
    FILE_PATH="${FILE_TEMPLATE//\{FHR3\}/$FHR3}"
    FILE_PATH="${FILE_PATH//\{FHR\}/$FHR}"
    if [[ ! -s "${FILE_PATH}" ]]; then
        echo "GRIB2 file missing: ${FILE_PATH}" >&2; exit 4
    fi
fi

if [[ "${MODEL}" == "aigfsv1" && -n "${TEMPLATE_SFC_RAW:-}" ]]; then
    SFC_FILE_TEMPLATE="${TEMPLATE_SFC_RAW//\{IDATE\}/$IDATE}"
    SFC_FILE_TEMPLATE="${SFC_FILE_TEMPLATE//\{IHOUR\}/$IHOUR}"
    SFC_FILE_TEMPLATE="${SFC_FILE_TEMPLATE//\{HEAD_AIGFSV1\}/$HEAD_AIGFSV1}"
    if [[ "${VAR_LC}" != "timeheight" ]]; then
        SFC_FILE_PATH="${SFC_FILE_TEMPLATE//\{FHR3\}/$FHR3}"
        SFC_FILE_PATH="${SFC_FILE_PATH//\{FHR\}/$FHR}"
        if [[ ! -s "${SFC_FILE_PATH}" ]]; then
            echo "Surface GRIB2 file missing: ${SFC_FILE_PATH}" >&2; exit 4
        fi
    fi
fi

expand_template_keep_fhr3() {
    local s="$1"
    s="${s//\{IDATE\}/$IDATE}"
    s="${s//\{IHOUR\}/$IHOUR}"

    s="${s//\{HEAD_ARAFS\}/${HEAD_ARAFS:-}}"
    s="${s//\{HEAD_GFSV16\}/${HEAD_GFSV16:-}}"
    s="${s//\{HEAD_GFSV17\}/${HEAD_GFSV17:-}}"
    s="${s//\{HEAD_GDAS\}/${HEAD_GDAS:-}}"
    s="${s//\{HEAD_AIGFSV1\}/${HEAD_AIGFSV1:-}}"

    echo "$s"
}

# 5) Hand off to plotting shell wrapper (plugin per VAR)
PLOT_SH="${TOPDIR}/ush/plot_${VAR_LC}.sh"
GENERIC_SH="${TOPDIR}/ush/plot_generic.sh"

if [[ -x "${PLOT_SH}" ]]; then
  TARGET_SH="${PLOT_SH}"
elif [[ -x "${GENERIC_SH}" ]]; then
  TARGET_SH="${GENERIC_SH}"
  echo "WARNING: ${PLOT_SH##*/} not found; using plot_generic.sh for VAR=${VAR}" >&2
else
  echo "ERROR: No plotting wrapper found for VAR=${VAR} (expected ${PLOT_SH##*/} or plot_generic.sh)" >&2
  exit 5
fi

EXTRA_SLP_ARGS=()
if [[ -n "${SFC_FILE_PATH}" ]]; then
    EXTRA_SLP_ARGS+=( --sfc-file "${SFC_FILE_PATH}" )
fi

EXTRA_PY_ARGS=()
if [[ -n "${UNITS:-}" ]]; then
    EXTRA_PY_ARGS+=( --units "${UNITS}" )
fi


if [[ "${VAR_LC}" == "timeheight" ]]; then
   METE_MODELS="${MODELS:-${MODEL}}"

   METE_ARGS=()
   [[ -n "${TEMPLATE_ARAFS:-}" ]]        && METE_ARGS+=( --template-arafs "$(expand_template_keep_fhr3 "${TEMPLATE_ARAFS}")" )
   [[ -n "${TEMPLATE_GFSV16:-}" ]]       && METE_ARGS+=( --template-gfsv16 "$(expand_template_keep_fhr3 "${TEMPLATE_GFSV16}")" )
   [[ -n "${TEMPLATE_GFSCTL:-}" ]]       && METE_ARGS+=( --template-gfsctl "$(expand_template_keep_fhr3 "${TEMPLATE_GFSCTL}")" )
   [[ -n "${TEMPLATE_GFSDENY:-}" ]]      && METE_ARGS+=( --template-gfsdeny "$(expand_template_keep_fhr3 "${TEMPLATE_GFSDENY}")" )
   [[ -n "${TEMPLATE_GFSV17:-}" ]]       && METE_ARGS+=( --template-gfsv17 "$(expand_template_keep_fhr3 "${TEMPLATE_GFSV17}")" )
   [[ -n "${TEMPLATE_GDAS:-}" ]]         && METE_ARGS+=( --template-gdas "$(expand_template_keep_fhr3 "${TEMPLATE_GDAS}")" )
   [[ -n "${TEMPLATE_URMA:-}" ]]         && METE_ARGS+=( --template-urma "$(expand_template_keep_fhr3 "${TEMPLATE_URMA}")" )
   [[ -n "${TEMPLATE_RAP:-}" ]]          && METE_ARGS+=( --template-rap "$(expand_template_keep_fhr3 "${TEMPLATE_RAP}")" )
   [[ -n "${TEMPLATE_AIGFSV1_PRES:-}" ]] && METE_ARGS+=( --template-aigfsv1-pres "$(expand_template_keep_fhr3 "${TEMPLATE_AIGFSV1_PRES}")" )
   [[ -n "${TEMPLATE_AIGFSV1_SFC:-}" ]]  && METE_ARGS+=( --template-aigfsv1-sfc "$(expand_template_keep_fhr3 "${TEMPLATE_AIGFSV1_SFC}")" )

   "${TARGET_SH}" \
     --models "${METE_MODELS}" \
     --date-type "${DATE_TYPE}" \
     --idate "${IDATE}" --ihour "${IHOUR}" \
     --fhrs "${FHRS}" \
     --lat "${POINT_LAT}" --lon "${POINT_LON}" \
     --home "${HOME_DIR}" \
     --tmp "${TMP}" \
     --comout "${COMOUT}" \
     --fix "${FIX}" \
     "${METE_ARGS[@]}"
else
    "${TARGET_SH}" \
      --model "${MODEL}" \
      --file "${FILE_PATH}" \
      --date-type "${DATE_TYPE}" \
      --idate "${IDATE}" --ihour "${IHOUR}" \
      --vdate "${VDATE}" --vhour "${VHOUR}" \
      --fhr "${FHR}" \
      --var "${VAR}" \
      --domain "${DOMAIN}" \
      --home "${HOME_DIR}" \
      --comout "${COMOUT}" \
      --tmp "${TMP}" \
      --fix "${FIX}" \
      --quiver-stride "${QUIVER_STRIDE}" \
      --slp-contours "${SLP_CONTOURS}" \
      --bool_analysis "${BOOL_ANALYSIS}" \
      "${EXTRA_PY_ARGS[@]}" "${EXTRA_SLP_ARGS[@]}"
fi
