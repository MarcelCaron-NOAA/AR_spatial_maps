#!/bin/bash
# dev/run_daily_wcoss2_qsubs.sh

set -euo pipefail

export BASEDIR="/lfs/h2/emc/vpppg/save/marcel.caron/AR_spatial_maps"
export LOGDIR="/lfs/h2/emc/stmp/marcel.caron/arafs_output/logs"
export PLOTSDIR="/lfs/h2/emc/stmp/marcel.caron/arafs_output/plots"
mkdir -p "${LOGDIR}" "${PLOTSDIR}"
cd "${LOGDIR}"

PDY=$(date -u -d "today" +%Y%m%d)

declare -a PDYm
for i in {1..2}; do
  PDYm[$i]=$(date -u -d "$i days ago" +%Y%m%d)
done

coords=(
    "-150.02 61.17"
    "-139.68 59.50"
    "-122.30 47.45"
    "-122.60 45.58"
    "-121.50 38.52"
)

export FHRS="0-120:6"

for DATE in "${PDYm[@]}" "$PDY"; do
    export YEAR="${DATE:0:4}"
    export MONTH="${DATE:4:2}"
    export DAY="${DATE:6:2}"
    for HOUR in 00; do
        export HOUR="${HOUR}"
        for MODELS in "gfsv16,arafs"; do
        # --------- 2) FORECAST RUNS (BOOL_ANL=FALSE) ----------
          export MODELS="${MODELS}"
          for VARIABLE in TIMEHEIGHT; do
              export VARIABLE=${VARIABLE}
              for coord in "${coords[@]}"; do
               export POINT_LON="${coord%% *}"
               export POINT_LAT="${coord##* }"
               qsub -V $BASEDIR/dev/drive_wcoss2_default.sh
              done
          done
        done
    done
done
