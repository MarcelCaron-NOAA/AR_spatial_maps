#!/bin/bash
#PBS -N default_plot
#PBS -j oe
#PBS -q dev
#PBS -A VERF-DEV
#PBS -l walltime=03:00:00
#PBS -l place=vscatter:exclhost,select=1:ncpus=1:ompthreads=1:mem=150GB
#PBS -l debug=true

# Load environment
set -euxo pipefail

# Go to your working directory
export BASEDIR="${BASEDIR:-/lfs/h2/emc/vpppg/save/marcel.caron/AR_spatial_maps}"
cd $BASEDIR || exit 1

# Optional: print node info and environment
echo "Running on host: $(hostname)"
echo "Start time: $(date)"
echo "Working directory: $(pwd)"
echo

export YEAR=${YEAR:-2025}
export MONTH=${MONTH:-11}
export DAY=${DAY:-01}
export HOUR=${HOUR:-00}
export FHOUR=${FHOUR:-24}
export VARIABLE=${VARIABLE:-IVT}
export MOD=${MOD:-gfsv16}
export MODELS=${MODELS:-"gfsv16,arafs"}
export POINT_LON=${POINT_LON:-"-145.0"}
export POINT_LAT=${POINT_LAT:-"60.0"}
export BOOL_ANL=${BOOL_ANL:-FALSE}
export QUIVER_STRIDE=${QUIVER_STRIDE:-10}

# Run the plotting driver with your config
bash scripts/ex-plots.sh parm/config.wcoss2.test

# Done
echo
echo "Job completed at: $(date)"

