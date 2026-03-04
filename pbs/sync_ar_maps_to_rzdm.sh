#!/bin/bash
set -euo pipefail

SRC="/lfs/h2/emc/stmp/marcel.caron/arafs_output/plots/"
DST="mcaron@emcrzdm.ncep.noaa.gov:/home/www/emc/htdocs/users/verification/regional/ar/maps/images/"

#rsync -avz --update --ignore-existing "$SRC" "$DST"
rsync -avz --update "$SRC" "$DST"

SRC="/lfs/h2/emc/ptmp/xingren.wu/plots/"
rsync -avz --update "$SRC" "$DST"
