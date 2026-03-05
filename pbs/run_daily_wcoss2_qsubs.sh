#!/bin/bash
# dev/run_daily_wcoss2_qsubs.sh

set -euo pipefail

BASEDIR="/lfs/h2/emc/vpppg/save/$USER/AR_spatial_maps"
LOGDIR="/lfs/h2/emc/stmp/$USER/arafs_output/logs"
PLOTSDIR="/lfs/h2/emc/stmp/$USER/arafs_output/plots"
mkdir -p "${LOGDIR}" "${PLOTSDIR}"
cd "${LOGDIR}"

PDY=$(date -u -d "today" +%Y%m%d)

declare -a PDYm
for i in {1..8}; do
  PDYm[$i]=$(date -u -d "$i days ago" +%Y%m%d)
done

for DATE in "${PDYm[@]}" "$PDY"; do
    YEAR=${DATE:0:4}
    MONTH=${DATE:4:2}
    DAY=${DATE:6:2}
    for HOUR in 00 12; do
        export HOUR=${HOUR}

        # --------- 1) ANALYSIS RUNS (BOOL_ANL=TRUE) ----------
        for FHOUR in 0; do
          for VARIABLE in IWV IVT; do
            for MOD in gdas; do
              qsub -v BASEDIR=${BASEDIR},YEAR=${YEAR},MONTH=${MONTH},DAY=${DAY},HOUR=${HOUR},FHOUR=${FHOUR},VARIABLE=${VARIABLE},MOD=${MOD},BOOL_ANL=TRUE,QUIVER_STRIDE=10 \
                $BASEDIR/dev/drive_wcoss2_default.sh
            done
          done
        done

        # --------- 2) FORECAST RUNS (BOOL_ANL=FALSE) ----------
        # Equivalent to:
        for FHOUR in 0 6 12 18 24 30 36 42 48 54 60 66 72 78 84 90 96 102 108 114 120; do
          for VARIABLE in IWV IVT; do
            for MOD in gfsv16 gfsv17 aigfsv1; do
              qsub -v BASEDIR=${BASEDIR},YEAR=${YEAR},MONTH=${MONTH},DAY=${DAY},HOUR=${HOUR},FHOUR=${FHOUR},VARIABLE=${VARIABLE},MOD=${MOD},BOOL_ANL=FALSE,QUIVER_STRIDE=10 \
                $BASEDIR/dev/drive_wcoss2_default.sh
            done
          done
        done
    done
    for HOUR in 06 12 18; do
        export HOUR=${HOUR}

        # --------- 1) ANALYSIS RUNS (BOOL_ANL=TRUE) ----------
        for FHOUR in 0; do
          for VARIABLE in IWV IVT; do
            for MOD in gdas; do
              qsub -v BASEDIR=${BASEDIR},YEAR=${YEAR},MONTH=${MONTH},DAY=${DAY},HOUR=${HOUR},FHOUR=${FHOUR},VARIABLE=${VARIABLE},MOD=${MOD},BOOL_ANL=TRUE,QUIVER_STRIDE=10 \
                $BASEDIR/dev/drive_wcoss2_default.sh
            done
          done
        done
    done
done
