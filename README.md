# AR Plotting Package

**Author:** Marcel Caron  
**Purpose:** Generate gridded plots of derived atmospheric variables for AR events from GRIB2 forecast files for various models (ARAFS, GFSv16, GFSv17).

---

## Overview

This package provides a modular workflow to plot gridded maps from GRIB2 forecast data.  
It supports multiple models and automatically handles environment setup, configuration, file path resolution, and plotting.

The system is organized as follows:

```
AR_spatial_maps/
│
├── versions/ ← Environment setup
│ └── run.env
│
├── parm/ ← Config files
│ ├── config.example
│ ├── config.gfsv16
│ ├── config.arafs
│ └── ...
│
├── scripts/ ← Executable driver scripts
│ └── ex-plots.sh
│
├── ush/ ← Plotting and utility code
│ ├── ivt_from_grib.py
│ ├── plot_util.py
│ ├── plot_generic.sh
│ ├── plot_ivt.sh
│ └── dicts.py
│
├── fix/ ← Static resources (e.g., logos)
│ └── noaa.png
│
└── dev/drive_ivt.sh ← Example PBS job submission script
```

---

## 1. Environment Setup

The package uses NOAA’s **Lmod** module system.  
To set up your environment manually, source the provided file:

```bash
source versions/run.env
```

This loads all required dependencies (Python, grib2io, Cartopy, etc.) and defines $PYTHONPATH so that utilities are available to the plotting scripts.

---

## 2. Configuration

All run-time parameters (date, model, domain, directories, etc.) are specified in the config file under parm/.

Example: parm/config.arafs
```bash
MODEL=arafs
DATE=20251106
HOUR=00
DATE_TYPE=INIT
FHR=24
VAR=IVT
DOMAIN=npac
HOME_DIR=/lfs/h2/emc/vpppg/noscrub/${USER}/ARAFS_spatial_maps
COMOUT=/lfs/h2/emc/stmp/arafs_output/plots
TMP=/lfs/h2/emc/stmp/${USER}/tmp
FIX=${HOME_DIR}/fix
```

Each model also defines a GRIB2 file template:
```bash
HEAD_ARAFS=/lfs/h2/emc/ptmp/xingren.wu/ARAFS_NRT_2025
TEMPLATE_ARAFS={HEAD_ARAFS}/{IDATE}{IHOUR}/00E/{IDATE}{IHOUR}.arafs.parent.atm.f{FHR3}.grb2
```

---

## 3. Running the Plotter
### Interactive (command line)
```bash
scripts/ex-plots.sh parm/config.arafs
```

This:
1. Sources the module environment (versions/run.env)

2. Loads configuration (parm/config.arafs)

3. Computes INIT/VALID times and builds the GRIB2 path

4. Runs the plotting script (ush/ivt_from_grib.py)

---

### Batch mode

(Preferred most of the time, particularly for high-resolution models)

WCOSS2:
```bash
qsub dev/drive_ivt.sh
```

Logs are written to 
```php-template
ivt_arafs_plot.o<jobid>
```

---

## 4. Output

Plots are saved under $COMOUT in model/date-organized folders, e.g.:

```swift
/lfs/h2/emc/stmp/${USER}/arafs_output/plots/20251106/
└── ivt.arafs.2025110600.f024.npacific.png
```

Each plot includes:

- Shaded IVT magnitude (kg m⁻¹ s⁻¹)

- IVT vector field (quivers)

- Sea-level pressure contours

- NOAA logo and vector reference box

- Initialization and valid timestamps

---

## 5. Extending the System

To add a new variable:

1. Create a color map and thresholds if needed in plot_util.py → cmaps(var)

2. Add a shell wrapper ush/plot_<var>.sh (copy from plot_ivt.sh)

3. Add a plotting script for that variable ush/<var>_from_grib.py (copy from ivt_from_grib.py)

4. Point to the correct GRIB2 fields in your variable-specific Python logic

5. Update the config file (VAR=<var>)

---

## Contact

For questions or contributions, reach out to:
**Marcel Caron**
NOAA/EMC Model Evaluation Group (MEG)
marcel.caron@noaa.gov

---

## Quick Start Summary

```bash
git clone <repo>
cd AR_spatial_maps
bash scripts/ex-plots.sh parm/config.test
```
