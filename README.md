# AR Spatial Maps Plotting Package

**Author:** Marcel Caron  
**Purpose:** Generate gridded plots and meteograms of atmospheric river diagnostics and related meteorological variables from GRIB2 forecast data.

This repository supports automated graphics production for several forecast models on NOAA HPC systems (WCOSS2 and Gaea).

---

# Overview

This package provides a modular workflow to:

- Read GRIB2 forecast data
- Compute derived atmospheric river diagnostics
- Produce publication-ready plots
- Run operational graphics generation via cron or scron jobs

Currently supported plot types include:

| Variable | Description |
|--------|--------|
| IVT | Integrated Vapor Transport |
| IWV | Integrated Water Vapor |
| T2M | 2-meter temperature |
| T850 | 850-mb temperature |
| WIND10M | 10-meter wind |
| Time-height | RH time-height cross sections with precip / IVT / IWV meteograms |

Supported forecast systems currently include:

- ARAFS
- GDAS
- GFSv16
- GFSv17
- AIGFS

---

# Repository Structure

```
AR_spatial_maps/
│
├── dev/                 Example driver scripts
├── fix/                 Static resources (logos etc.)
├── modulefiles/         Environment module setup
├── parm/                Runtime configuration files
├── pbs/                 WCOSS2 batch and transfer scripts
├── scripts/             Main execution drivers
├── slurm/               Gaea SLURM job drivers
├── ush/                 Plotting scripts and utilities
├── versions/            Versioned environment definitions
│
└── README.md
```

Important directories:

**parm/**  
Runtime configuration files defining model, variable, date, domain, directories, etc.

**ush/**  
Core plotting code and utilities.

**pbs/**  
WCOSS2 batch submission and file transfer scripts.

**slurm/**  
Gaea SLURM job scripts.

---

# Environment Setup

Environment modules are provided for both supported systems.

## WCOSS2

```
source versions/wcoss2.ver
```

## Gaea

```
source versions/gaeac6.ver
```

These load:

- Python environment
- grib2 utilities
- Cartopy / plotting libraries
- required module dependencies

---

# Configuration

Runtime configuration is controlled via files in:

```
parm/
```

Example configuration:

```
MODEL=arafs
DATE=20251106
HOUR=00
DATE_TYPE=INIT
FHR=24
VAR=IVT
DOMAIN=npac

HOME_DIR=/path/to/repo
COMOUT=/output/location
TMP=/scratch/tmp
FIX=${HOME_DIR}/fix
```

Each configuration also defines a GRIB2 template used to locate model data.

Example:

```
HEAD_ARAFS=/path/to/arafs/data
TEMPLATE_ARAFS={HEAD_ARAFS}/{IDATE}{IHOUR}/00E/{IDATE}{IHOUR}.arafs.parent.atm.f{FHR3}.grb2
```

---

# Running Plots

## Interactive execution

```
scripts/ex-plots.sh parm/config.arafs.ivt
```

The driver script performs:

1. environment loading
2. configuration parsing
3. forecast/valid time calculation
4. GRIB file resolution
5. execution of the appropriate plotting script

---

# Operational Workflows

Operational graphics are typically generated through scheduled job submission loops.

---

# WCOSS2 Setup

Login to WCOSS2 (dev).

Clone the repository:

```
cd /lfs/h2/emc/vpppg/save/<USER>
git clone https://github.com/MarcelCaron-NOAA/AR_spatial_maps.git
```

Edit the batch submission script if needed:

```
vi pbs/run_daily_wcoss2_qsubs.sh
```

Ensure `BASEDIR` and `USER` are correct.

Create output directories:

```
cd /lfs/h2/emc/stmp/<USER>

mkdir -p arafs_output/logs
mkdir -p arafs_output/plots
mkdir -p cron.out
```

Add cron job:

```
crontab -e
```

Add:

```
30 7,8,20 * * * /bin/bash /lfs/h2/emc/vpppg/save/<USER>/AR_spatial_maps/pbs/run_daily_wcoss2_qsubs.sh >> /lfs/h2/emc/stmp/<USER>/cron.out/run_daily_ar_maps.log 2>&1
```

---

# Gaea Setup

Login to Gaea C6.

Clone the repository:

```
cd ~
git clone https://github.com/MarcelCaron-NOAA/AR_spatial_maps.git
```

Create working directories:

```
cd /gpfs/f6/ar-cpu/scratch/<USER>

mkdir -p scron.out
mkdir -p AR_maps/logs
mkdir -p AR_maps/plots
```

Edit scrontab:

```
scrontab -e
```

Add:

```
#SCRON -J submit_loop -A ar-cpu -p cron_c6 --nodes=1 --ntasks-per-node=1 --cpus-per-task=1 --time=00:10:00 -o /gpfs/f6/scratch/<USER>/scron.out/submit_loop.%j.scron.out

26 7,8,9,23 * * * /ncrc/home1/<USER>/AR_spatial_maps/slurm/submit_loop.slurm
```

If graphics stop updating, check whether **scron lines have been disabled**.  
This has been a recurring issue on Gaea.

---

# Data Availability Timing

Typical availability windows:

| Model | Forecast Cycle | Data Available |
|------|------|------|
| AR-AFS | 00Z / 12Z | ~06Z / 00Z |
| GFS | 00Z / 12Z | ~06Z / 18Z |

---

# File Transfers

## Gaea → Ursa (via Globus)

Create target directory on Ursa:

```
mkdir -p /scratch4/NCEPDEV/fv3-cam/<USER>/AR_Project/plots
```

Open:

```
https://globus.org
```

Steps:

1. Log in with NOAA credentials
2. Open **File Manager**
3. Select collection:

```
noaardhpcs#gaea_f6
```

Navigate to:

```
/gpfs/f6/ar-cpu/scratch/<USER>/AR_maps
```

Select `plots`.

Transfer to:

```
noaardhpcs#ursa
```

Destination:

```
/scratch4/NCEPDEV/fv3-cam/<USER>/AR_Project
```

Recommended transfer options:

- Sync level L2
- Preserve source modification times
- Skip files with errors
- Fail on quota errors

Set transfer timer to repeat every **1 hour**.

---

## WCOSS2 → RZDM

Edit transfer script:

```
vi pbs/sync_ar_maps_to_rzdm.sh
```

Set:

```
rzdm_account=<your_username>
```

Add cron job:

```
crontab -e
```

Add:

```
15 * * * * /lfs/h2/emc/vpppg/save/<USER>/AR_spatial_maps/pbs/sync_ar_maps_to_rzdm.sh >> /lfs/h2/emc/stmp/<USER>/cron.out/sync_ar_maps_to_rzdm.sh 2>&1
```

---

# Output

Graphics are written to the configured `$COMOUT` directory.

Example:

```
/lfs/h2/emc/stmp/<USER>/arafs_output/plots/20251106/
```

Example file:

```
ivt.arafs.2025110600.f024.npacific.png
```

Plots typically include:

- shaded field values
- vector overlays where appropriate
- pressure contours
- NOAA logo
- initialization and valid timestamps

---

# Contact

For questions or contributions:

**Marcel Caron**  
NOAA Environmental Modeling Center  
Model Evaluation Group  

marcel.caron@noaa.gov
