#!/bin/bash
#PBS -N ivt_arafs_plot
#PBS -j oe
#PBS -q dev
#PBS -A VERF-DEV
#PBS -l walltime=00:30:00
#PBS -l place=vscatter:exclhost,select=1:ncpus=1:ompthreads=1:mem=150GB
#PBS -l debug=true

# Load environment
set -euxo pipefail

# Go to your working directory
cd /lfs/h2/emc/vpppg/noscrub/marcel.caron/AR_spatial_maps || exit 1

# Optional: print node info and environment
echo "Running on host: $(hostname)"
echo "Start time: $(date)"
echo "Working directory: $(pwd)"
echo

# Run the plotting driver with your config
bash scripts/ex-plots.sh parm/config.gfsv16.ivt

# Done
echo
echo "Job completed at: $(date)"

