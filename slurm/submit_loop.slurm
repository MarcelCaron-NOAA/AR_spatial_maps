#!/bin/bash
#SBATCH -M c6                              
#SBATCH -J submit_loop                    
#SBATCH -o %x.o%j.out                         
#SBATCH -e %x.o%j.out
#SBATCH -t 00:10:00                          
#SBATCH -A ar-cpu                          
#SBATCH -p batch                               
#SBATCH --nodes=1                            
#SBATCH --ntasks-per-node=1                  
#SBATCH --cpus-per-task=1                    

date

PDY=$(date -u +%Y%m%d)
PDYm1=$(date -u -d "1 day ago" +%Y%m%d)
PDYm2=$(date -u -d "2 days ago" +%Y%m%d)
PDYm3=$(date -u -d "3 days ago" +%Y%m%d)
PDYm4=$(date -u -d "4 days ago" +%Y%m%d)
PDYm5=$(date -u -d "5 days ago" +%Y%m%d)
PDYm6=$(date -u -d "6 days ago" +%Y%m%d)
PDYm7=$(date -u -d "7 days ago" +%Y%m%d)
PDYm8=$(date -u -d "8 days ago" +%Y%m%d)

HOMEDIR="/ncrc/home1/Marcel.Caron/AR_spatial_maps"
SCRATCHDIR="/gpfs/f6/scratch/Marcel.Caron/ar-cpu/AR_maps"

for FHOUR in 0 6 12 18 24 30 36 42 48 54 60 66 72 78 84 90 96 102 108 114 120; do
    export FHOUR=${FHOUR}
    for IDATE in "${PDY}" "${PDYm1}" "${PDYm2}" "${PDYm3}" "${PDYm4}" "${PDYm5}" "${PDYm6}" "${PDYm7}" "${PDYm8}"; do
        export YEAR=${IDATE:0:4}
        export MONTH=${IDATE:4:2}
        export DAY=${IDATE:6:2}
        PLOTDIR="${SCRATCHDIR}/plots/${IDATE}"
        for IHOUR in 00 12; do
            export IHOUR="${IHOUR}"

            # If the directory exists AND contains at least one regular file, skip
            if [ -d "$PLOTDIR" ] && find "$PLOTDIR" -type f | read -r _; then
                echo "Skipping ${IDATE} F${FHOUR} — plots already exist in ${PLOTDIR}"
                continue
            fi
            for VARIABLE in IVT IWV; do
                sbatch --export=FHOUR=${FHOUR},YEAR=${YEAR},MONTH=${MONTH},DAY=${DAY},VARIABLE=${VARIABLE},IHOUR=${IHOUR} \
                    -o ${SCRATCHDIR}/logs/%x.o%j.out ${HOMEDIR}/slurm/drive_gaea_routine.slurm
            done
        done
    done
done
