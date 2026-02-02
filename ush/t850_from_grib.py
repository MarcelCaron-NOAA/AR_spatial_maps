#!/usr/bin/env python3
import os, argparse
import numpy as np
import grib2io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from datetime import datetime
import plot_util as util
import dicts

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--date-type", required=True, choices=["INIT","VALID"])
    ap.add_argument("--idate", required=True); ap.add_argument("--ihour", required=True)
    ap.add_argument("--vdate", required=True); ap.add_argument("--vhour", required=True)
    ap.add_argument("--fhr", type=int, required=True)

    ap.add_argument("--var", default="T850")
    ap.add_argument("--domain", default="conusw")

    ap.add_argument("--out", required=False)
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--bool_analysis", default="")

    ap.add_argument("--units", default="C", choices=["K","C","F"],
                    help="Output units for 850-mb temperature (default: C).")

    ap.add_argument("--home", default=os.getenv("HOME", ""))
    ap.add_argument("--comout", default=None)
    ap.add_argument("--fix", default="")
    ap.add_argument("--quiver-stride", type=int, default=10,
                    help="Stride for wind barbs (plot every Nth grid point).")

    # Optional override, but defaults to 850 to keep script name meaningful
    ap.add_argument("--level-mb", type=int, default=850,
                    help="Isobaric level in mb (default: 850).")

    args, unknown = ap.parse_known_args()
    if unknown:
        print(f"NOTE: ignoring unrecognized args: {' '.join(unknown)}")

    outdir = args.out or args.comout
    if not outdir:
        ap.error("One of --out or --comout is required")
    os.makedirs(outdir, exist_ok=True)

    gf = grib2io.open(args.file, mode="r")

    # -------------------------
    # Fetch TMP at 850 mb
    # -------------------------
    level_mb = int(args.level_mb)
    target_lbl = f"{level_mb} mb"

    t_by = util.read_msgs_by_name_and_level(gf, "TMP", return_msgs=False)
    if target_lbl not in t_by:
        raise RuntimeError(f"TMP not found at {target_lbl}")

    t_msgs = util.read_msgs_by_name_and_level(gf, "TMP", return_msgs=True)
    ref_msg = t_msgs[target_lbl]
    LON, LAT = util._latlon_from_msg_safe(ref_msg)
    if LON is None or LAT is None:
        raise RuntimeError("Could not build lon/lat grid for 850-mb TMP.")

    T = t_by[target_lbl]

    # Units conversion (TMP is typically Kelvin)
    u = (args.units or "C").upper()
    if np.nanmean(T) > 150.0:
        Tc = T - 273.15
    else:
        Tc = T  # already in C-ish

    if u == "K":
        Tplot = Tc + 273.15
        units_out = "K"
        zeroC_level = 273.15
    elif u == "F":
        Tplot = Tc * 9.0/5.0 + 32.0
        units_out = "°F"
        zeroC_level = 32.0
    else:
        Tplot = Tc
        units_out = "°C"
        zeroC_level = 0.0

    print(np.array(Tplot).shape, np.array(LON).shape, np.array(LAT).shape)

    # -------------------------
    # Plot settings
    # -------------------------
    current_dpi = 100
    base_dpi = 100
    scaling_factor = (current_dpi / base_dpi) ** 0.1
    util.set_params(current_dpi, scaling_factor)

    datacrs = ccrs.PlateCarree()
    dom = dicts.domains(args.domain.lower())
    proj = dom["ccrs"]
    extent = dom["extent"]
    figsize = dom.get("figsize", (10., 8.5))
    dx = dom["xticks"]
    dy = dom["yticks"]

    fig = plt.figure(figsize=figsize)
    ax = plt.axes(projection=proj)
    util.draw_basemap(
        ax, datacrs=datacrs, extent=extent, xticks=dx, yticks=dy,
        left_lats=True, right_lats=False, grid=True,
        scaling_factor=scaling_factor
    )

    # -------------------------
    # Colormap
    # -------------------------
    # Prefer a dedicated cmap if you add one; otherwise fall back to t2m.
    try:
        cmap, norm, bnds, cbarticks, cbarlbl, cmap_units = dicts.cmaps("t850")
    except Exception:
        cmap, norm, bnds, cbarticks, cbarlbl, cmap_units = dicts.cmaps("t2m")

    cbarlbl = f"{level_mb}-mb Temperature ({units_out})"

    # -------------------------
    # Shaded field + contours
    # -------------------------
    cf = ax.contourf(
        LON, LAT, Tplot,
        cmap=cmap, norm=norm, levels=bnds,
        alpha=0.8, extend="both",
        transform=datacrs, zorder=10
    )

    # Light isotherms (use bnds as-isotherms, you can change later)
    cs_t = ax.contour(
        LON, LAT, Tplot,
        transform=datacrs, levels=bnds,
        colors="black", linewidths=0.3, alpha=0.8, zorder=11
    )

    # 0C isotherm (or equivalent in F/K)
    cs_0 = ax.contour(
        LON, LAT, Tplot,
        transform=datacrs, levels=[zeroC_level],
        colors="red", linewidths=0.6, alpha=0.9, zorder=12
    )

    # -------------------------
    # Colorbar
    # -------------------------
    bbox = ax.get_position()
    pad = 0.015
    width = 0.02
    cax = fig.add_axes([bbox.x1 + pad, bbox.y0, width, bbox.height])
    cb = plt.colorbar(
        cf, cax=cax, orientation="vertical", ticklocation="right",
        extendfrac=0.03, ticks=cbarticks, drawedges=True
    )
    cb.set_label(cbarlbl)
    cb.outline.set_edgecolor("black")
    cb.outline.set_linewidth(0.5)
    for e in cb.ax.collections:
        e.set_edgecolor("black")
        e.set_linewidth(0.3)

    # -------------------------
    # Optional: 850-mb wind barbs (no new args; uses --quiver-stride if provided)
    # -------------------------
    try:
        u_by = util.read_msgs_by_name_and_level(gf, "UGRD", return_msgs=False)
        v_by = util.read_msgs_by_name_and_level(gf, "VGRD", return_msgs=False)
        if target_lbl not in u_by or target_lbl not in v_by:
            raise RuntimeError(f"UGRD/VGRD not found at {target_lbl}")

        U = u_by[target_lbl]
        V = v_by[target_lbl]
        Ukn = util.wind_ms_to_knots(U)
        Vkn = util.wind_ms_to_knots(V)

        s = max(1, int(getattr(args, "quiver_stride", 10) or 10))

        # Use wind grid (safe across products)
        u_msgs = util.read_msgs_by_name_and_level(gf, "UGRD", return_msgs=True)
        ref_u = u_msgs[target_lbl]
        LONw, LATw = util._latlon_from_msg(ref_u)
        if LONw is None or LATw is None:
            LONw, LATw = LON, LAT

        bkw = util.default_barb_kwargs(args.model, scaling_factor=scaling_factor)
        bkw.update(dict(barbcolor="k", flagcolor="k", linewidth=0.8))

        ax.barbs(
            LONw[::s, ::s], LATw[::s, ::s],
            Ukn[::s, ::s], Vkn[::s, ::s],
            transform=datacrs, zorder=200, **bkw
        )
    except Exception as e:
        print(f"NOTE: skipping {level_mb}-mb wind barbs (could not plot): {e}")

    # -------------------------
    # Optional logo
    # -------------------------
    if args.fix:
        logo_path = os.path.join(args.fix, "noaa.png")
        if os.path.exists(logo_path):
            util.add_corner_logo(ax, logo_path, loc="upper left", frac=0.06, alpha=0.5)

    # -------------------------
    # Titles + output name
    # -------------------------
    var_info = {
        f"{level_mb}-mb Temperature": {"units": units_out, "feature": "shaded"}
    }
    var_string = util.get_var_string(var_info)

    iday, vday = (args.idate[6:], args.vdate[6:])
    imonth, vmonth = (
        datetime.strptime(args.idate, "%Y%m%d").strftime("%B"),
        datetime.strptime(args.vdate, "%Y%m%d").strftime("%B")
    )
    iyear, vyear = (args.idate[:4], args.vdate[:4])
    model_string = dicts.get_model_name(args.model)

    if args.bool_analysis == "TRUE" and int(args.fhr) == 0:
        title_left = f"Initialized: {args.ihour}Z {iday} {imonth} {iyear} (Analysis)"
        ofn = f"{args.var}.{args.model}.{args.idate}{args.ihour}.anl.{args.domain}.png"
    else:
        title_left = f"Initialized: {args.ihour}Z {iday} {imonth} {iyear} (F{int(args.fhr):03d})"
        ofn = f"{args.var}.{args.model}.{args.idate}{args.ihour}.f{int(args.fhr):03d}.{args.domain}.png"

    title_right = f"Valid: {args.vhour}Z {vday} {vmonth} {vyear}"
    title_main = f"{model_string} {var_string}"
    default_fs = matplotlib.rcParams["axes.titlesize"]
    main_fs = default_fs * 1.4

    ax.set_title(title_right, loc="right")
    ax.set_title(title_left, loc="left")
    ax.set_title(title_main + "\n", fontsize=main_fs, loc="center")

    ofp = os.path.join(outdir, ofn)
    plt.savefig(ofp)
    plt.close(fig)
    os.chmod(ofp, 0o755)
    print(f"New image created: {ofp}")

if __name__ == "__main__":
    main()

