#!/bin/bash
#SBATCH -M c6                              
#SBATCH -J ivt_arafs_plot                    
#SBATCH -o %x-%j.out                         
#SBATCH -e %x-%j.out
#SBATCH -t 00:30:00                          
#SBATCH -A ar-cpu                          
#SBATCH -p batch                               
#SBATCH --nodes=1                            
#SBATCH --ntasks-per-node=1                  
#SBATCH --cpus-per-task=1                    
#SBATCH --exclusive                          

# Load environment
set -euo pipefail
export OMP_NUM_THREADS=1

# Go to your working directory
cd /ncrc/home1/$USER/AR_spatial_maps || exit 1

# Optional: print node info and environment
echo "Running on host: $(hostname)"
echo "Start time: $(date)"
echo "Working directory: $(pwd)"
echo

export DAY=${DAY:-01}
export FHOUR=${FHOUR:-24}
export VARIABLE=${VARIABLE:-IVT}
export QUIVER_STRIDE=${QUIVER_STRIDE:-"83"}

# Run the plotting driver with your config
bash scripts/ex-plots.sh parm/config.gaea.default

# Done
echo
echo "Job completed at: $(date)"

