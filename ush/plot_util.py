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
    # last resort: reopen by path if available
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

    # Build from type/value/unit if available (as in your Section 4 dump)
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
        if tfs == 103:  # Spec height above MSL (gpm) – uncommon in your files
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
    """Build lon/lat 2D arrays from a representative message’s grid metadata."""
    nx = getattr(msg, "nx", None); ny = getattr(msg, "ny", None)
    lon0 = getattr(msg, "longitudeFirstGridpoint", None)
    lon1 = getattr(msg, "longitudeLastGridpoint", None)
    lat0 = getattr(msg, "latitudeFirstGridpoint", None)
    lat1 = getattr(msg, "latitudeLastGridpoint", None)
    if None in (nx, ny, lon0, lon1, lat0, lat1):
        return None, None
    # Handle wrap (e.g., 0..359.75)
    lons = np.linspace(lon0, lon1, int(nx), endpoint=True)
    lats = np.linspace(lat0, lat1, int(ny), endpoint=True)
    LON, LAT = np.meshgrid(lons, lats)
    return LON, LAT


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
            # Robust: just skip bad messages, but leave a breadcrumb if you’re debugging
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

    # *** key change: sort to ASCENDING pressure so dp > 0 ***
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
    # (use the first item in flist AFTER reordering)
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
    # Reuse your field collector; only SPFH is needed here
    plevels, q_by, _, _ = _collect_pl_fields(gf)

    # Filter to requested span
    flist = [(lbl, p) for (lbl, p) in plevels if pmax_mb <= p <= pmin_mb]
    if len(flist) < 2:
        raise RuntimeError("Not enough levels in requested pressure range for IWV.")

    # Arrays
    Ps_mb = np.array([p for _, p in flist])                 # (L,)
    Qs    = np.array([q_by[lbl] for lbl, _ in flist])       # (L, ny, nx)  kg/kg

    # *** key: ASCENDING pressure so dp > 0 ***
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
    Prefers PRMSL, falls back to MSLET.  Raises RuntimeError if neither exist.
    """
    # Try PRMSL first
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

    # Try MSLET next
    out = read_msgs_by_name_and_level(gf, "MSLET")
    if out:
        slp = list(out.values())[0]
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
        "gfsv17": 5,    
        "gdas": 5,    
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
    # Make the reference arrow a touch thicker
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
        
        ax.set_xticks(xticks, crs=datacrs)
        ax.set_yticks(yticks, crs=datacrs)
        ax.ticklabel_format(axis='both', style='plain')

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
        ax.set_extent(extent, crs=datacrs)  # example for CONUS_West; we’ll parameterize later
        
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
    # tweak length by model density if you like
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

