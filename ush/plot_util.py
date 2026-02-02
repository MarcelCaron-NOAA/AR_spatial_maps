#!/usr/bin/env python3
import os, sys, argparse, math
import numpy as np
import grib2io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.ticker as mticker
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import FancyBboxPatch
from matplotlib.colors import ListedColormap
from PIL import Image
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER

def _rewind_grib(gf):
    """Best-effort rewind for grib2io handle."""
    try:
        # grib2io often supports seek
        gf.seek(0)
        return
    except Exception:
        pass
    try:
        gf.rewind()
        return
    except Exception:
        pass
    # last resort
    try:
        import grib2io
        path = getattr(gf, "name", None)
        if path:
            gf.close()
            return grib2io.open(path, 'r')
    except Exception:
        pass
    return gf  # if all else fails, return as-is

def _get_attr(msg, *names, default=None):
    for n in names:
        if hasattr(msg, n):
            return getattr(msg, n)
    return default

def _level_label(msg):
    """Produce a stable text label for the message’s level."""
    # Direct 'level' string (often already like '1 mb', 'surface', etc.)
    lvl = _get_attr(msg, "level", default=None)
    if isinstance(lvl, str) and lvl.strip():
        return lvl.strip()

    # Build from type/value/unit if available
    tfs  = _get_attr(msg, "typeOfFirstFixedSurface", default=None)
    ufs  = _get_attr(msg, "unitOfFirstFixedSurface", default=None)  # e.g., 'Pa' or 'mb'
    vfs  = _get_attr(msg, "valueOfFirstFixedSurface", default=None)
    # Some stacks store scaledValue* plus scaleFactor*
    svfs = _get_attr(msg, "scaledValueOfFirstFixedSurface", default=None)
    sff  = _get_attr(msg, "scaleFactorOfFirstFixedSurface", default=None)

    # Common cases
    if isinstance(tfs, int):
        if tfs == 1:  # Ground/surface of the earth
            return "surface"
        if tfs == 100:  # Isobaric surface in Pa
            # Prefer human-readable mb/hPa if we can compute it
            if vfs is not None:
                try:
                    # vfs may already be Pa; convert to mb
                    p_pa = float(vfs)
                    p_mb = p_pa / 100.0 if p_pa > 200 else p_pa  # guard if already in mb
                    return f"{int(round(p_mb))} mb"
                except Exception:
                    pass
            if svfs is not None and sff is not None:
                try:
                    p_pa = float(svfs) * (10 ** int(sff))
                    p_mb = p_pa / 100.0
                    return f"{int(round(p_mb))} mb"
                except Exception:
                    pass
            return "isobaric"
        if tfs == 10:  # Entire atmosphere (column)
            return "entire atmosphere (considered as a single layer)"
        if tfs == 103:  # Spec height above MSL (gpm)
            return f"{int(float(vfs))} gpm" if vfs is not None else "height(msl)"
        if tfs == 107:  # Hybrid level, etc.
            return "hybrid"

    # Fallbacks
    if vfs is not None and isinstance(ufs, str):
        return f"{vfs} {ufs}"
    return str(lvl) if lvl is not None else "unknown"

def _parse_mb(level_str):
    """Extract pressure (mb) from labels like '700 mb'. Return float or None."""
    try:
        s = level_str.strip().lower().replace('hpa','mb')
        if ' mb' in s:
            return float(s.split(' mb')[0])
    except Exception:
        pass
    return None

def _collect_pl_fields(gf):
    """Get dicts of q, u, v on isobaric levels { '<p> mb': ndarray }."""
    q_by = read_msgs_by_name_and_level(gf, "SPFH")  # kg/kg
    u_by = read_msgs_by_name_and_level(gf, "UGRD")  # m/s
    v_by = read_msgs_by_name_and_level(gf, "VGRD")  # m/s
    # Keep only common isobaric levels
    levels = set(q_by.keys()) & set(u_by.keys()) & set(v_by.keys())
    plevels = [(lbl, _parse_mb(lbl)) for lbl in levels]
    plevels = [(lbl, p) for (lbl, p) in plevels if p is not None]
    if not plevels:
        raise RuntimeError("No common isobaric SPFH/UGRD/VGRD levels found.")
    # Sort from high pressure (near surface) to low pressure (aloft)
    plevels.sort(key=lambda t: -t[1])
    return plevels, q_by, u_by, v_by

def _latlon_from_msg(msg):
    """
    Build lon/lat 2D arrays from a representative message’s grid metadata.

    Priority:
      1) Use message-provided lat/lon arrays if available (best for projected grids like URMA).
      2) Fallback to first/last gridpoint + linspace (works for regular lat/lon grids).
    """
    # ---- 1) Direct lat/lon arrays (preferred; handles URMA/projections) ----
    for attr in ("latlons", "latlons()", "get_latlons", "grid_latlons"):
        try:
            if attr.endswith("()"):
                fn = getattr(msg, attr[:-2], None)
                if callable(fn):
                    LAT, LON = fn()  # many libs return (lat, lon)
                else:
                    continue
            else:
                obj = getattr(msg, attr, None)
                if callable(obj):
                    LAT, LON = obj()
                else:
                    # sometimes stored as tuple already
                    if isinstance(obj, (tuple, list)) and len(obj) == 2:
                        LAT, LON = obj
                    else:
                        continue

            if LAT is not None and LON is not None:
                LAT = np.asarray(LAT)
                LON = np.asarray(LON)
                if LAT.size > 0 and LON.size > 0 and LAT.shape == LON.shape:
                    return LON, LAT
        except Exception:
            pass

    # ---- 2) Fallback: regular lat/lon grid endpoints ----
    nx = getattr(msg, "nx", None) or getattr(msg, "Ni", None)
    ny = getattr(msg, "ny", None) or getattr(msg, "Nj", None)

    lon0 = getattr(msg, "longitudeFirstGridpoint", None)
    lon1 = getattr(msg, "longitudeLastGridpoint", None)
    lat0 = getattr(msg, "latitudeFirstGridpoint", None)
    lat1 = getattr(msg, "latitudeLastGridpoint", None)

    if None in (nx, ny, lon0, lon1, lat0, lat1):
        return None, None

    nx = int(nx); ny = int(ny)

    lons = np.linspace(float(lon0), float(lon1), nx, endpoint=True)
    lats = np.linspace(float(lat0), float(lat1), ny, endpoint=True)
    LON, LAT = np.meshgrid(lons, lats)
    return LON, LAT

def _latlon_from_msg_safe(msg):
    """
    Robust lon/lat getter.
    - For projected/curvilinear grids (e.g., RAP awip32 Lambert), try message-provided
      lat/lon first (grid/latlons/latitude/longitude arrays).
    - Fall back to the simple linear meshgrid method (_latlon_from_msg) for regular lat/lon grids.
    """
    import numpy as np

    # 1) Try "grid" or "latlons" style APIs (callable or attribute)
    for attr in ("latlons", "grid"):
        if hasattr(msg, attr):
            obj = getattr(msg, attr)
            try:
                out = obj() if callable(obj) else obj
                if isinstance(out, tuple) and len(out) == 2:
                    a, b = out
                    a = np.asarray(a); b = np.asarray(b)

                    # Heuristic to decide which is lat vs lon
                    # lat should be within [-90, 90] almost always
                    if np.nanmin(a) >= -90 and np.nanmax(a) <= 90:
                        LAT, LON = a, b
                    elif np.nanmin(b) >= -90 and np.nanmax(b) <= 90:
                        LON, LAT = a, b
                    else:
                        # ambiguous; assume (lon,lat)
                        LON, LAT = a, b

                    if LON.size and LAT.size:
                        return LON, LAT
            except Exception:
                pass

    # 2) Try direct arrays on the message
    for lon_name, lat_name in (
        ("longitude", "latitude"),
        ("longitudes", "latitudes"),
        ("lon", "lat"),
    ):
        if hasattr(msg, lon_name) and hasattr(msg, lat_name):
            LON = np.asarray(getattr(msg, lon_name))
            LAT = np.asarray(getattr(msg, lat_name))
            if LON.size and LAT.size:
                return LON, LAT

    # 3) Last resort: your old regular-grid approximation
    return _latlon_from_msg(msg)

def read_msgs_by_name_and_level(gf, var_name, want_levels=None, casefold=True, return_msgs=False):
    """
    Scan a grib2io file handle for all messages matching var_name.
    Returns dict[level_label] -> message (or ndarray if return_msgs=False and msg has .values()).

    Parameters
    ----------
    gf : grib2io.Grib2File (open for reading)
    var_name : str  (e.g., 'SPFH', 'UGRD', 'VGRD')
    want_levels : set/list of strings to keep (optional, compare after _level_label)
    casefold : bool  compare var names case-insensitively
    return_msgs : bool  if True, store the message objects; else try to store data arrays.
    """
    # Ensure we’re at BOF
    new_gf = _rewind_grib(gf) or gf
    gf = new_gf

    target = {}
    name_goal = var_name.lower() if casefold else var_name

    # Some grib2io versions iterate via for msg in gf; others need range(gf.messages)
    # Use the iterator protocol; if that fails, fall back to indexed access.
    def _iter_msgs(handle):
        try:
            for msg in handle:
                yield msg
        except TypeError:
            # fall back if not iterable
            for i in range(getattr(handle, "messages", 0)):
                yield handle.message(i)

    for msg in _iter_msgs(gf):
        try:
            sname = _get_attr(msg, "shortName", "name", "parameterName", default=None)
            if not sname:
                continue
            comp = sname.lower() if casefold else sname
            if comp != name_goal:
                continue

            lvl = _level_label(msg)
            if want_levels and lvl not in want_levels:
                continue

            if return_msgs:
                target[lvl] = msg
            else:
                # Try to extract data array without loading lat/lon unless needed
                data = None
                for attr in ("values", "data", "get_values"):
                    try:
                        maybe = getattr(msg, attr)
                        data = maybe() if callable(maybe) else maybe
                        break
                    except Exception:
                        continue
                target[lvl] = data if data is not None else msg  # last-ditch: keep msg
        except Exception as e:
            # print(f"SKIP msg due to error: {e}", file=sys.stderr)
            continue

    # Rewind again so subsequent reads (e.g., UGRD/VGRD) see the full file
    _rewind_grib(gf)
    return target

def get_field(msg):
    """Return data array as float32 masked array"""
    arr = msg.getdata().astype(np.float32)
    return np.ma.masked_where(~np.isfinite(arr), arr)

def read_prmsl(gf):
    for msg in gf:
        try:
            if msg.parameter_abbrev.upper() in ("PRMSL","MSLET"):
                # Pa; convert to hPa
                pr = get_field(msg) / 100.0
                lats, lons = msg.latlons()
                return pr, lats, lons
        except Exception:
            continue
    return None, None, None

def compute_ivt(gf, pmin_mb=1000, pmax_mb=200, g=9.80665):
    """
    IVT = (1/g) ∫ q * V * dp
    Sign-safe: integrates with ascending pressure so dp > 0.
    Returns IVT_u, IVT_v, IVT_mag, (LON,LAT)
    """
    plevels, q_by, u_by, v_by = _collect_pl_fields(gf)

    # filter to requested span
    flist = [(lbl, p) for (lbl, p) in plevels if pmax_mb <= p <= pmin_mb]
    if len(flist) < 2:
        raise RuntimeError("Not enough levels in requested pressure range for IVT.")

    # arrays
    Ps_mb = np.array([p for _, p in flist])                  # (L,)
    Qs    = np.array([q_by[lbl] for lbl, _ in flist])        # (L,ny,nx)
    Us    = np.array([u_by[lbl] for lbl, _ in flist])        # (L,ny,nx)
    Vs    = np.array([v_by[lbl] for lbl, _ in flist])        # (L,ny,nx)

    order = np.argsort(Ps_mb)                                # low→high index order
    Ps_pa = Ps_mb[order].astype(float) * 100.0               # (L,)
    Qs, Us, Vs = Qs[order], Us[order], Vs[order]

    # trapezoidal layer means
    dp   = np.diff(Ps_pa)                                    # (L-1,), all > 0
    qbar = 0.5 * (Qs[:-1] + Qs[1:])
    ubar = 0.5 * (Us[:-1] + Us[1:])
    vbar = 0.5 * (Vs[:-1] + Vs[1:])
    dp3  = dp[:, None, None]

    IVTu = np.nansum(qbar * ubar * dp3, axis=0) / g
    IVTv = np.nansum(qbar * vbar * dp3, axis=0) / g
    IVT  = np.hypot(IVTu, IVTv)

    # Sanity check: make sure that IVT vectors are correlated with 850-mb wind
    try:
        u850 = read_msgs_by_name_and_level(gf, "UGRD")["850 mb"]
        v850 = read_msgs_by_name_and_level(gf, "VGRD")["850 mb"]
        corr = np.nanmean(
            (IVTu*u850 + IVTv*v850) /
            (np.hypot(IVTu, IVTv)*np.hypot(u850, v850) + 1e-9)
        )
        if np.isfinite(corr) and corr < -0.2:
    	    IVTu, IVTv = -IVTu, -IVTv
    	    print("[compute_ivt] Flipped IVT sign (negative correlation vs 850-mb wind).")
    except Exception:
        pass

    # reference grid from a U message at any included level
    ref_lbl = flist[order[0]][0]
    ref_msg = read_msgs_by_name_and_level(gf, "UGRD", return_msgs=True)[ref_lbl]
    LON, LAT = _latlon_from_msg(ref_msg)
    return IVTu, IVTv, IVT, (LON, LAT)

def compute_iwv(gf, pmin_mb=1000, pmax_mb=200, g=9.80665):
    """
    IWV (a.k.a. precipitable water) = (1/g) ∫ q dp
    Sign-safe: integrates with ascending pressure so dp > 0.
    Returns IWV_mm, (LON, LAT)
    """
    plevels, q_by, _, _ = _collect_pl_fields(gf)

    # Filter to requested span
    flist = [(lbl, p) for (lbl, p) in plevels if pmax_mb <= p <= pmin_mb]
    if len(flist) < 2:
        raise RuntimeError("Not enough levels in requested pressure range for IWV.")

    # Arrays
    Ps_mb = np.array([p for _, p in flist])                 # (L,)
    Qs    = np.array([q_by[lbl] for lbl, _ in flist])       # (L, ny, nx)  kg/kg

    order = np.argsort(Ps_mb)                               # low→high
    Ps_pa = Ps_mb[order].astype(float) * 100.0              # Pa
    Qs    = Qs[order]

    # Trapezoidal layer means
    dp   = np.diff(Ps_pa)                                   # (L-1,), positive
    qbar = 0.5 * (Qs[:-1] + Qs[1:])
    dp3  = dp[:, None, None]

    IWV_kgm2 = np.nansum(qbar * dp3, axis=0) / g           # kg m^-2
    IWV_mm   = IWV_kgm2                                     # 1 kg m^-2 == 1 mm

    # Clean tiny negative numerical noise
    IWV_mm = np.where(IWV_mm < 0, np.clip(IWV_mm, 0, None), IWV_mm)

    # Lon/lat from a representative SPFH message (after reordering)
    ref_lbl = flist[order[0]][0]
    ref_msg = read_msgs_by_name_and_level(gf, "SPFH", return_msgs=True)[ref_lbl]
    LON, LAT = _latlon_from_msg(ref_msg)
    return IWV_mm, (LON, LAT)

def fetch_slp(gf):
    """
    Return 2-D array of mean-sea-level pressure (hPa) and the units string.
    Prefers MSLET, falls back to PRMSL.  Raises RuntimeError if neither exist.
    """

    # Try MSLET first
    out = read_msgs_by_name_and_level(gf, "MSLET")
    if out:
        slp = list(out.values())[0]
        if np.nanmedian(slp) > 5e4:
            slp = slp / 100.0
            units = "hPa"
        else:
            units = "Pa"
        return slp, units

    # Try PRMSL next
    out = read_msgs_by_name_and_level(gf, "PRMSL")
    if out:
        slp = list(out.values())[0]
        # GFS PRMSL is in Pa → convert to hPa
        if np.nanmedian(slp) > 5e4:
            slp = slp / 100.0
            units = "hPa"
        else:
            units = "Pa"
        return slp, units

    raise RuntimeError("Neither PRMSL nor MSLET found for SLP contours.")

def fetch_uv_at_level(gf, level_mb=850):
    """
    Return (U, V, (LON, LAT)) for a single isobaric level (mb).
    """
    target_lbl = f"{int(level_mb)} mb"
    u_by = read_msgs_by_name_and_level(gf, "UGRD", return_msgs=False)
    v_by = read_msgs_by_name_and_level(gf, "VGRD", return_msgs=False)
    if target_lbl not in u_by or target_lbl not in v_by:
        raise RuntimeError(f"UGRD/VGRD not found at {target_lbl}")
    # Build lon/lat from one of the wind messages at that level
    u_msgs = read_msgs_by_name_and_level(gf, "UGRD", return_msgs=True)
    ref_msg = u_msgs[target_lbl]
    LON, LAT = _latlon_from_msg(ref_msg)
    return u_by[target_lbl], v_by[target_lbl], (LON, LAT)

def fetch_tmp_2m(gf, units="C"):
    """
    Fetch 2-m temperature (TMP at 2 m above ground).
    Returns: (T2m, (LON, LAT), units_out)
    """
    target_lbl = "2 m above ground"

    # Pull values dict keyed by level label
    t_by = read_msgs_by_name_and_level(gf, "TMP", return_msgs=False)
    if target_lbl not in t_by:
        # be a little flexible across products
        alt_keys = [k for k in t_by.keys() if "2 m" in k and "ground" in k]
        if alt_keys:
            target_lbl = alt_keys[0]
        else:
            raise RuntimeError("TMP not found at 2 m above ground")

    # Get the reference message at that level for lon/lat
    t_msgs = read_msgs_by_name_and_level(gf, "TMP", return_msgs=True)
    if target_lbl not in t_msgs:
        # same fallback logic as above, in case keys differ slightly
        alt_keys = [k for k in t_msgs.keys() if "2 m" in k and "ground" in k]
        if alt_keys:
            target_lbl = alt_keys[0]
        else:
            raise RuntimeError("TMP message not found at 2 m above ground")

    ref_msg = t_msgs[target_lbl]
    LON, LAT = _latlon_from_msg(ref_msg)

    T = t_by[target_lbl]

    # --- Unit conversion ---
    u = (units or "C").upper()

    # Heuristic: most GRIB TMP is in Kelvin
    # If it's already in C/F, it won't exceed ~100 typically.
    if np.nanmean(T) > 150.0:
        Tc = T - 273.15
    else:
        Tc = T

    if u == "K":
        Tout = Tc + 273.15
        units_out = "K"
    elif u == "F":
        Tout = Tc * 9.0 / 5.0 + 32.0
        units_out = "F"
    else:
        Tout = Tc
        units_out = "C"

    return Tout, (LON, LAT), units_out

'''
def fetch_tmp_2m(gf, units="C"):
    """
    Fetch 2-m temperature TMP at 2 m above ground.
    Returns: (data2d, (lon2d, lat2d), units_out)
    """
    # --- your existing attempts first ---
    # e.g. try read_msgs_by_name_and_level(gf, "TMP", 2, "heightAboveGround") ...
    # if found -> return

    # --- fallback: scan all TMP messages and match the level string like wgrib2 shows ---
    tmp_candidates = []
    n = len(gf) if hasattr(gf, "__len__") else getattr(gf, "messages", 0)
    for i in range(int(n)):
        m = gf[i]
        name = getattr(m, "shortName", None) or getattr(m, "name", None) or getattr(m, "parameterName", None)
        if name != "TMP":
            continue

        lvl_txt = (
            getattr(m, "level", None)
            or getattr(m, "levelStr", None)
            or getattr(m, "level_string", None)
        )
        if lvl_txt and "2 m above ground" in str(lvl_txt):
            tmp_candidates.append(m)

    if tmp_candidates:
        m = tmp_candidates[0]
        var = m.data if not callable(getattr(m, "data", None)) else m.data()
        lons = np.linspace(
        LON, LAT = m.grid()
        # Units conversion (most likely Kelvin)
        units_out = units.upper()
        if units_out in ("C", "F"):
            # assume Kelvin if values look like Kelvin
            if float(var.mean()) > 150:
                var_c = var - 273.15
            else:
                var_c = var
            if units_out == "C":
                return var_c, (LON, LAT), "C"
            else:
                return (var_c * 9.0/5.0 + 32.0), (LON, LAT), "F"
        return var, (LON, LAT), "K"

    raise RuntimeError("2-m temperature (TMP at 2 m above ground) not found in GRIB2.")
'''

def extent_from_domain(domain):
    domains = {
        "CONUS_West": (-150.0, -115.0, 18.0, 60.0),
        "NEPAC": (110.0, -105.0, -10.0, 70.0),  # 110E wraps across 255E; use PlateCarree with wrapped longitudes later if needed
        "CONUS": (-130.0, -65.0, 20.0, 55.0),
        "EPAC": (-180.0, -100.0, -10.0, 70.0),
        "GLOBAL": (-180.0, 180.0, -70.0, 70.0),
    }
    return domains.get(domain, domains["CONUS_West"])

def add_map_feats(ax):
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), linewidth=0.6)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), linewidth=0.4)
    ax.add_feature(cfeature.STATES.with_scale("50m"), linewidth=0.2, edgecolor='gray')

def get_var_string(var_info):
    var_strings = []
    for var in var_info:
        if var_info[var]['units']:
            var_units = var_info[var]['units']
            if var_info[var]['feature']:
                var_feature = var_info[var]['feature']
                var_strings.append(f'{var} ({var_units}; {var_feature})')
            else:
                var_strings.append(f'{var} ({var_units})')
        elif var_info[var]['feature']:
            var_feature = var_info[var]['feature']
            var_strings.append(f'{var} ({var_feature})')
        else:
            var_strings.append(f'{var}')
    if len(var_strings) == 1:
        return var_strings[0]
    elif len(var_strings) == 2:
        return var_strings[0] + ' and ' + var_strings[1]
    else:
        return ', '.join(var_strings[:-1]) + f', and {var_strings[-1]}'

def choose_quiver_stride(model: str, user_qs: int = None, nx: int = None, ny: int = None) -> int:
    """
    Priority:
      1) explicit CLI value (user_qs) if provided
      2) per-model defaults
      3) auto-estimate from grid size if available
      4) sane fallback (10)
    """
    if user_qs is not None:
        return max(1, int(user_qs))

    # These are overridden by strides passed in config
    defaults = {
        "gfsv16": 5,       # 0.25° grid -> denser, so smaller stride
        "gfsctl": 5,       # 0.25° grid -> denser, so smaller stride
        "gfsdeny": 5,       # 0.25° grid -> denser, so smaller stride
        "gfsv17": 5,    
        "aigfsv1": 5,    
        "gdas": 5,    
        "urma": 83,    
        "rap": 40,    
        "arafs": 83,       # finer grid -> larger stride (thin more)
    }
    key = (model or "").lower()
    if key in defaults:
        return defaults[key]

    if nx and ny:
        target_x = 35
        target_y = 25
        sx = max(1, int(round(nx / target_x)))
        sy = max(1, int(round(ny / target_y)))
        return max(sx, sy)

    return 10

def set_params(current_dpi, scaling_factor):
    plt.rcParams.update({
                    'figure.dpi': current_dpi,
                    'font.size': 16 * scaling_factor, #changes contour and bbox text size
                    'axes.labelsize': 8 * scaling_factor,
                    'axes.titlesize': 10 * scaling_factor,
                    'xtick.labelsize': 14 * scaling_factor,
                    'ytick.labelsize': 14 * scaling_factor, #changes cbar labelsize
                    'legend.fontsize': 5 * scaling_factor,
                    'lines.linewidth': 0.7 * scaling_factor,
                    'axes.linewidth': 0.2 * scaling_factor,
                    'legend.fontsize': 12 * scaling_factor,
                    'xtick.major.width': 0.8 * scaling_factor,
                    'ytick.major.width': 0.8 * scaling_factor,
                    'xtick.minor.width': 0.6 * scaling_factor,
                    'ytick.minor.width': 0.6 * scaling_factor,
                    'lines.markersize': 6 * scaling_factor
                })

def add_quiver_key_box(ax, Q, ref=500, loc="upper right",
                       boxsize=(0.16, 0.12), pad=0.02, alpha=0.6,
                       units_text=r"kg m$^{-1}$ s$^{-1}$", lw=0.8):
    """
    Add a quiver reference arrow with a semi-transparent white bbox.

    Parameters
    ----------
    ax : matplotlib Axes
    Q : matplotlib.quiver.Quiver
        The Quiver returned by ax.quiver(...) (ensures consistent scaling).
    ref : float
        Reference vector magnitude to display.
    loc : {"upper right","upper left","lower right","lower left"}
    boxsize : (w, h)
        Box width/height in axes fraction.
    pad : float
        Padding from the axes edge in axes fraction units.
    alpha : float
        Background box alpha.
    units_text : str
        Units (second line under the number).
    lw : float
        Edge width for the reference arrow.
    """
    # Anchor box in axes coords
    loc = loc.lower().strip()
    eps = 0.005 # tiny inward nudge in axes-fraction units
    if loc == "upper right":
        x0, y0 = 1 - pad - boxsize[0] - eps, 1 - pad - boxsize[1] - eps
    elif loc == "upper left":
        x0, y0 = pad + eps, 1 - pad - boxsize[1] - eps
    elif loc == "lower right":
        x0, y0 = 1 - pad - boxsize[0] - eps, pad + eps
    elif loc == "lower left":  # "lower left"
        x0, y0 = pad + eps, pad + eps
    else:
        raise ValueError(f"Unknown loc='{loc}'")

    # Background rounded box
    box = FancyBboxPatch(
        (x0, y0), boxsize[0], boxsize[1],
        transform=ax.transAxes, boxstyle="square,pad=0.015",
        linewidth=0.4, edgecolor='black', facecolor=(1, 1, 1, alpha), zorder=1000
    )
    box.set_clip_on(True)
    ax.add_patch(box)

    # QuiverKey centered near top of the box
    X = x0 + boxsize[0] * 0.5
    Y = y0 + boxsize[1] * 0.72

    # Two-line label: value and units
    label = f"{int(ref)}\n{units_text}"

    qk = ax.quiverkey(
        Q, X, Y, ref, label,
        coordinates="axes", labelpos="S",
        fontproperties={"size": max(6, int(0.7 * plt.rcParams['font.size']))},
        labelsep=0.08,  # spacing between arrow and label
        color="k", zorder=1010
    )
    try:
        qk.set_zorder(1010)
        if hasattr(qk, "text"):
            qk.text.set_zorder(1011)
            qk.text.set_clip_on(False)
        for child in qk.get_children():
            child.set_zorder(1011)
            child.set_clip_on(False)
    except Exception:
        pass
    try:
        qk.vector.set_linewidth(lw)
    except Exception:
        pass

    return qk, box

def add_corner_logo(ax, img_path, loc="upper left", frac=0.12, alpha=0.5, pad=0.02):
    """
    Draw a semi-transparent logo in a plot corner.

    Parameters
    ----------
    ax : matplotlib Axes
    img_path : str
        Path to image (e.g., ".../fix/noaa.png").
    loc : {"upper left","upper right","lower left","lower right"}
    frac : float
        Target logo width as a fraction of the axes *pixel* width.
    alpha : float
        Logo transparency.
    pad : float
        Padding from the axes edge in axes fraction units.
    """
    # Ensure we know the axes pixel size
    fig = ax.figure
    fig.canvas.draw_idle()
    bbox = ax.get_window_extent()
    ax_w_px = bbox.width

    im = Image.open(img_path).convert("RGBA")
    # scale so displayed width = frac * axes pixel width
    target_px = frac * ax_w_px
    zoom = target_px / im.width

    # Corner anchor & box alignment
    loc = loc.lower().replace(" ", "")
    anchors = {
        "upperleft":   ((pad, 1 - pad), (0, 1)),
        "upperright":  ((1 - pad, 1 - pad), (1, 1)),
        "lowerleft":   ((pad, pad), (0, 0)),
        "lowerright":  ((1 - pad, pad), (1, 0)),
    }
    (xy, box_alignment) = anchors.get(loc, anchors["upperleft"])

    oi = OffsetImage(im, zoom=zoom, alpha=alpha, resample=True)
    ab = AnnotationBbox(
        oi, xy,
        xycoords="axes fraction",
        box_alignment=box_alignment,
        frameon=False,
    )
    ab.set_zorder(2000)
    ax.add_artist(ab)
    return ab

def draw_basemap(ax, datacrs=ccrs.PlateCarree(), extent=None, xticks=None, 
                 yticks=None, grid=False, left_lats=True, right_lats=False, 
                 bottom_lons=True, mask_ocean=False, coastline=True, 
                 scaling_factor=1.):
    kw_ticklabels = {
        'color': 'black', 
        'weight': 'normal', 
        'fontsize': 12 * scaling_factor
    }
    kw_grid = {'linewidth': 0.8, 'color':'k', 'linestyle':'--', 'alpha': 0.4, 'zorder': 100}
    kw_ticks = {'length': 4, 'width': 0.5, 'pad': 2, 'color': 'black',
                'labelcolor': 'dimgray'}

    mapcrs = ax.projection

    if isinstance(datacrs, str):
        s = datacrs.strip().lower()
        if s in ("platecarree", "platedcarree", "pc", "latlon", "lonlat", "geodetic", "epsg:4326"):
            datacrs = ccrs.PlateCarree()
        else:
            raise TypeError(f"draw_basemap: datacrs must be a cartopy CRS, got string: {datacrs}")
    if not hasattr(datacrs, "_as_mpl_transform"):
        raise TypeError(f"draw_basemap: datacrs must be a cartopy CRS object, got: {type(datacrs)}")

    ax.add_feature(cfeature.LAND, facecolor='0.9', zorder=5)
    ax.add_feature(cfeature.BORDERS, edgecolor='0.4', lw=0.8, zorder=15)
    ax.add_feature(cfeature.STATES, edgecolor='0.2', lw=0.2, zorder=14)
    if coastline == True:
        ax.add_feature(cfeature.COASTLINE, edgecolor='0.4', linewidth=0.8, zorder=16)
        ax.coastlines(resolution="50m")
    if mask_ocean == True:
        ax.add_feature(cfeature.OCEAN, edgecolor='0.4', zorder=6, facecolor='white')
    if mapcrs == ccrs.NorthPolarStereo(central_longitude=0.):
        gl = ax.gridlines(draw_labels=False,
                          linewidth=.5, color='black', alpha=0.5, linestyle='--')
    elif mapcrs == ccrs.SouthPolarStereo(central_longitude=0.):
        gl = ax.gridlines(draw_labels=True,
                          linewidth=.5, color='black', alpha=0.5, linestyle='--')
    else:
        gl = ax.gridlines(crs=datacrs, draw_labels=True, **kw_grid)
        gl.top_labels = False
        gl.left_labels = left_lats
        gl.right_labels = right_lats
        gl.bottom_labels = bottom_lons
        gl.xlocator = mticker.FixedLocator(xticks)
        gl.ylocator = mticker.FixedLocator(yticks)
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        gl.xlabel_style = kw_ticklabels
        gl.ylabel_style = kw_ticklabels
        plt.yticks(color='w', size=1)
        plt.xticks(color='w', size=1)
        
        # Some Cartopy projections cannot use set_xticks/set_yticks.  
        # Gridliner labels already handle ticks/labels robustly, so only do this when supported.
        try:
            ax.set_xticks(xticks, crs=datacrs)
            ax.set_yticks(yticks, crs=datacrs)
            ax.ticklabel_format(axis='both', style='plain')
        except RuntimeError:
            # keep gridliner labels; do not crash
            pass

    if (grid == True):
        gl.xlines = True
        gl.ylines = True
    else:
        gl.xlines = False
        gl.ylines = False

    try:
        for art in getattr(gl, "xline_artists", []) + getattr(gl, "yline_artists", []):
            art.set_zorder(100)
            art.set_clip_on(True)
    except Exception:
        pass

    if extent is None:
        ax.set_global()
        extent = [-180., 180., -90., 90.]
    else:
        ax.set_extent(extent, crs=datacrs)  # example for CONUS_West; parameterize later
        
    return ax

def wind_ms_to_knots(x):
    """Convert m/s to knots (vectorized)."""
    return np.asarray(x) * 1.9438444924406048

def default_barb_kwargs(model, scaling_factor=1.0):
    """
    Tuned, readable barb style. Returns kwargs for ax.barbs.
    - length scales a bit with figure scaling.
    - increments are in *knots* (since we convert U/V to knots).
    """
    # tweak length by model density if wanting
    base_len = 5.5
    if str(model).lower() == "arafs":
        base_len = 5.0  # a touch shorter for denser grid
    length = base_len * (0.95 + 0.1 * (scaling_factor ** 0.5))

    return dict(
        length=length,                 # barb length in points
        linewidth=0.5,
        pivot="middle",                # keeps shafts centered on points
        barb_increments=dict(half=5, full=10, flag=50),  # all in knots
        sizes={                        # fine-tune geometry
            "emptybarb": 0.08,
            "spacing":   0.20,
            "height":    0.35,
            "width":     0.25,
        }
    )

def normalize_lon_180(lon):
    """Return lon in [-180, 180). Accepts lon in any range."""
    lon = float(lon)
    lon = ((lon + 180.0) % 360.0) - 180.0
    return lon

def normalize_lon_360(lon):
    """Return lon in [0,360)."""
    lon = float(lon)
    lon = lon % 360.0
    if lon < 0:
        lon += 360.0
    return lon

def ij_from_latlon_regular_ll(msg, lat, lon):
    """
    Fast nearest-neighbor i,j for regular lat/lon GRIB2 grid (GDT=0 style).
    Uses msg latitudeFirstGridpoint/LastGridpoint, longitudeFirstGridpoint/LastGridpoint, nx, ny.
    """
    nx = int(getattr(msg, "nx"))
    ny = int(getattr(msg, "ny"))
    lon0 = float(getattr(msg, "longitudeFirstGridpoint"))
    lon1 = float(getattr(msg, "longitudeLastGridpoint"))
    lat0 = float(getattr(msg, "latitudeFirstGridpoint"))
    lat1 = float(getattr(msg, "latitudeLastGridpoint"))

    # Determine if longitudes are 0..360-ish
    lon_in = float(lon)
    if lon0 >= 0.0 and lon1 > 180.0:
        lon_in = normalize_lon_360(lon_in)
    else:
        lon_in = normalize_lon_180(lon_in)

    # Grid spacing
    dlon = (lon1 - lon0) / (nx - 1)
    dlat = (lat1 - lat0) / (ny - 1)  # note: often negative

    # Nearest index
    i = int(np.rint((lon_in - lon0) / dlon))
    j = int(np.rint((lat - lat0) / dlat))

    i = max(0, min(nx - 1, i))
    j = max(0, min(ny - 1, j))
    return j, i

def fetch_psfc_mb_point(gf, j, i):
    """
    Best-effort surface pressure at point in mb.
    Tries common shortNames: PRES, PSFC. Returns np.nan if not found.
    """
    for name in ("PRES", "PSFC"):
        d = read_msgs_by_name_and_level(gf, name)
        if not d:
            continue

        # Prefer an explicit surface key if present
        for key in ("surface", "0-0 m above ground"):
            if key in d:
                val = float(d[key][j, i])
                return val / 100.0 if val > 2000 else val  # Pa -> mb if needed

        # Fallback: if only one level exists, take it
        if len(d) == 1:
            arr = next(iter(d.values()))
            val = float(arr[j, i])
            return val / 100.0 if val > 2000 else val

    return np.nan

def compute_ivt_iwv_point(gf, j, i, pmin_mb=1000, pmax_mb=200, g=9.80665):
    q_by = read_msgs_by_name_and_level(gf, "SPFH")
    u_by = read_msgs_by_name_and_level(gf, "UGRD")
    v_by = read_msgs_by_name_and_level(gf, "VGRD")

    # surface-pressure-aware bottom bound
    psfc_mb = fetch_psfc_mb_point(gf, j, i)
    if np.isfinite(psfc_mb):
        pmin_eff = min(float(pmin_mb), float(psfc_mb))
    else:
        pmin_eff = float(pmin_mb)

    levels = set(q_by.keys()) & set(u_by.keys()) & set(v_by.keys())
    levs = []
    for lbl in levels:
        p = _parse_mb(lbl)
        if p is None:
            continue
        # use pmin_eff instead of pmin_mb
        if pmax_mb <= p <= pmin_eff:
            levs.append((lbl, p))

    if len(levs) < 2:
        raise RuntimeError("Not enough SPFH/UGRD/VGRD levels for IVT/IWV point calc.")

    Ps_mb = np.array([p for _, p in levs], dtype=float)
    order = np.argsort(Ps_mb)        # ascending so dp>0
    Ps_pa = Ps_mb[order] * 100.0

    q = np.array([q_by[levs[k][0]][j, i] for k in order], dtype=float)
    u = np.array([u_by[levs[k][0]][j, i] for k in order], dtype=float)
    v = np.array([v_by[levs[k][0]][j, i] for k in order], dtype=float)

    dp   = np.diff(Ps_pa)
    qbar = 0.5 * (q[:-1] + q[1:])
    ubar = 0.5 * (u[:-1] + u[1:])
    vbar = 0.5 * (v[:-1] + v[1:])

    ivtu = np.nansum(qbar * ubar * dp) / g
    ivtv = np.nansum(qbar * vbar * dp) / g
    ivt  = float(np.hypot(ivtu, ivtv))

    iwv  = float(np.nansum(qbar * dp) / g)
    return ivt, iwv

def fetch_apcp_surface_point(gf, j, i):
    """
    Return accumulated precip (mm) at the point if APCP exists.
    Many GRIBs store APCP in kg/m^2 which is mm for water.
    """
    apcp = read_msgs_by_name_and_level(gf, "APCP")
    # Common labels: "surface" or "0-0 m above ground" depending on file.
    for key in ("surface", "0-0 m above ground"):
        if key in apcp:
            return float(apcp[key][j, i])
    # fallback: if only one level exists, use that
    if len(apcp) == 1:
        return float(next(iter(apcp.values()))[j, i])
    return np.nan

def freezing_pressure_hpa(p_levels_hpa, tmp_TL, t0_k=273.15):
    """
    Compute freezing-level pressure (hPa) vs time from temperature on isobaric levels.

    Parameters
    ----------
    p_levels_hpa : 1D array (L,) descending, e.g. [1000, 925, ..., 200]
    tmp_TL : 2D array (T,L) temperature in Kelvin (NaN allowed)
    t0_k : float, freezing threshold (default 273.15 K)

    Returns
    -------
    p_freeze : 1D array (T,) pressure (hPa) where temp crosses t0_k.
               If near-sfc is <= freezing OR no crossing exists, returns NaN for that time.
    """
    p = np.asarray(p_levels_hpa, dtype=float)
    T = np.asarray(tmp_TL, dtype=float)

    if T.ndim != 2 or T.shape[1] != p.shape[0]:
        raise ValueError("tmp_TL must be (T,L) matching p_levels_hpa length")

    out = np.full((T.shape[0],), np.nan, dtype=float)

    for ti in range(T.shape[0]):
        prof = T[ti, :]

        # Require at least some valid values
        if not np.isfinite(prof).any():
            continue

        # "Near-surface": highest pressure level in your list (first element, since descending)
        T_sfc = prof[0]
        if np.isfinite(T_sfc) and T_sfc <= t0_k:
            # As requested: if surface is freezing, don't draw a line (comes out of ground later)
            continue

        # Find crossing between adjacent levels (descending p: index 0 is near surface)
        # We want a sign change in (T - t0)
        d = prof - t0_k

        # Must have two finite points for interpolation
        for k in range(len(p) - 1):
            if not (np.isfinite(d[k]) and np.isfinite(d[k+1])):
                continue

            # crossing if sign change or one exactly zero
            if d[k] == 0:
                out[ti] = p[k]
                break
            if d[k] * d[k+1] < 0:
                # linear interpolation in temperature between levels k and k+1
                # p_cross = p_k + frac*(p_{k+1}-p_k)
                frac = (t0_k - prof[k]) / (prof[k+1] - prof[k])
                out[ti] = p[k] + frac * (p[k+1] - p[k])
                break

        # If no crossing found: stays NaN (either all above or all below or missing)
    return out

def _time_edges(dts):
    """
    Given center times dts (len T), return bin edges (len T+1) where each bin spans
    halfway to neighbors. Works for irregular spacing too.
    """
    if len(dts) == 1:
        # arbitrary +/- 30 min if only one time
        from datetime import timedelta
        return [dts[0] - timedelta(minutes=30), dts[0] + timedelta(minutes=30)]

    edges = []
    # left edge
    edges.append(dts[0] - (dts[1] - dts[0]) / 2)
    # interior midpoints
    for k in range(len(dts) - 1):
        edges.append(dts[k] + (dts[k+1] - dts[k]) / 2)
    # right edge
    edges.append(dts[-1] + (dts[-1] - dts[-2]) / 2)
    return edges


def _contiguous_true_runs(mask):
    """
    Given boolean mask (len T), return list of (start_idx, end_idx) inclusive for True runs.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.size == 0:
        return []
    idx = np.where(mask)[0]
    if idx.size == 0:
        return []
    # split where gaps > 1
    breaks = np.where(np.diff(idx) > 1)[0]
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends   = np.r_[idx[breaks], idx[-1]]
    return list(zip(starts, ends))

def _pick_level_key(level_dict, want_contains):
    """
    Helper: find a key in level_dict whose text contains all substrings in want_contains.
    Returns the first match or None.
    """
    keys = list(level_dict.keys())
    want = [w.lower() for w in want_contains]
    for k in keys:
        kl = str(k).lower()
        if all(w in kl for w in want):
            return k
    return None

def fetch_uv_10m(gf):
    """
    Return (U10, V10, (LON, LAT)) for 10 m above ground.
    Uses UGRD/VGRD and builds lon/lat from the U message.
    """
    u_by = read_msgs_by_name_and_level(gf, "UGRD", return_msgs=False)
    v_by = read_msgs_by_name_and_level(gf, "VGRD", return_msgs=False)

    # Canonical key most products use
    lvl = "10 m above ground"
    if lvl not in u_by or lvl not in v_by:
        # be tolerant: find something that contains "10 m" and "ground"
        lvl2 = _pick_level_key(u_by, ["10 m", "ground"])
        if not lvl2 or lvl2 not in v_by:
            raise RuntimeError("10-m winds not found (UGRD/VGRD at 10 m above ground).")
        lvl = lvl2

    # Lon/lat from a representative U message
    u_msgs = read_msgs_by_name_and_level(gf, "UGRD", return_msgs=True)
    ref_msg = u_msgs[lvl]
    LON, LAT = _latlon_from_msg(ref_msg)
    if LON is None or LAT is None:
        raise RuntimeError("Could not build lon/lat for 10-m winds.")

    return u_by[lvl], v_by[lvl], (LON, LAT)

def fetch_wspd_10m(gf, units="kt", U10=None, V10=None):
    """
    Compute 10-m wind speed from U10/V10.
    units: 'ms' or 'kt' (default kt)
    Returns (WSPD, units_str)
    """
    if U10 is None or V10 is None:
        U10, V10, _ = fetch_uv_10m(gf)

    wspd_ms = np.sqrt(np.asarray(U10)**2 + np.asarray(V10)**2)

    units = (units or "kt").lower()
    if units in ("kt", "kts", "knots"):
        return wind_ms_to_knots(wspd_ms), "kt"
    else:
        return wspd_ms, "m/s"

def fetch_mslp(gf):
    """
    Fetch mean sea level pressure in hPa and lon/lat.
    Prefers MSLET if available, otherwise PRMSL.
    Returns: (SLP_hpa, (LON, LAT), name_used)
    """
    # Try MSLET (often "mean sea level" level label)
    for name in ("MSLET", "PRMSL"):
        try:
            by = read_msgs_by_name_and_level(gf, name, return_msgs=False)
            msgs = read_msgs_by_name_and_level(gf, name, return_msgs=True)

            # Find a reasonable key (usually 'mean sea level' or 'MSL')
            # Your read_msgs_by_name_and_level keys are level labels; scan them.
            key = None
            for k in by.keys():
                kl = str(k).lower()
                if "mean sea level" in kl or "msl" in kl:
                    key = k
                    break
            if key is None:
                # some products use 'surface' for PRMSL-ish fields; allow it
                key = list(by.keys())[0]

            ref_msg = msgs[key]
            LON, LAT = _latlon_from_msg(ref_msg)
            if LON is None or LAT is None:
                raise RuntimeError(f"Could not build lon/lat for {name}.")

            slp = np.asarray(by[key])

            # Units: PRMSL/MSLET are typically Pa. Convert to hPa if needed.
            # Heuristic: if mean is ~100000, it's Pa.
            if np.nanmean(slp) > 2000.0:
                slp_hpa = slp / 100.0
            else:
                slp_hpa = slp

            return slp_hpa, (LON, LAT), name
        except Exception:
            continue

    raise RuntimeError("No MSLP field found (tried MSLET, PRMSL).")
