#!/bin/bash
set -euo pipefail

rzdm_account="mcaron"
SRC="/lfs/h2/emc/stmp/$USER/arafs_output/plots/"
DST="${rzdm_account}@emcrzdm.ncep.noaa.gov:/home/www/emc/htdocs/users/verification/regional/ar/maps/images/"

#rsync -avz --update --ignore-existing "$SRC" "$DST"
rsync -avz --update "$SRC" "$DST"

SRC="/lfs/h2/emc/ptmp/xingren.wu/plots/"
rsync -avz --update "$SRC" "$DST"
