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

    ap.add_argument("--var", default="WIND10M")
    ap.add_argument("--domain", default="conusw")

    ap.add_argument("--out", required=False)
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--bool_analysis", default="")

    ap.add_argument("--home", default=os.getenv("HOME", ""))
    ap.add_argument("--comout", default=None)
    ap.add_argument("--fix", default="")

    ap.add_argument("--quiver-stride", type=int, default=10,
                    help="Stride for wind barbs (plot every Nth grid point).")

    ap.add_argument("--wspd-units", default="kt", choices=["ms","kt"],
                    help="Units for shaded 10-m wind speed: ms or kt (default: kt).")

    ap.add_argument("--slp-contours", nargs="*", default=[],
                    help="Optional list of SLP contour levels in hPa (e.g., 980 988 996 ...). "
                         "If not provided, uses a reasonable default.")
    ap.add_argument("--slp-contour-interval", type=float, default=4.0,
                    help="If --slp-contours not set, contour every N hPa (default 4).")
    ap.add_argument("--slp-contour-min", type=float, default=960.0)
    ap.add_argument("--slp-contour-max", type=float, default=1040.0)

    args, unknown = ap.parse_known_args()
    if unknown:
        print(f"NOTE: ignoring unrecognized args: {' '.join(unknown)}")

    outdir = args.out or args.comout
    if not outdir:
        ap.error("One of --out or --comout is required")
    os.makedirs(outdir, exist_ok=True)

    slp_levs=[]
    if args.slp_contours:
        if len(args.slp_contours) == 1 and isinstance(args.slp_contours[0], str) and " " in args.slp_contours[0]:
            slp_levs = [float(x) for x in args.slp_contours[0].split()]
        else:
            slp_levs = [float(x) for x in args.slp_contours]
    args.slp_contours = slp_levs

    gf = grib2io.open(args.file, mode="r")

    # -------------------------
    # Fetch 10-m winds + speed
    # -------------------------
    U10, V10, (LON, LAT) = util.fetch_uv_10m(gf)  # U/V in m/s typically
    WSPD, wspd_units = util.fetch_wspd_10m(gf, units=args.wspd_units, U10=U10, V10=V10)

    print("U10/V10/WSPD shapes:", np.asarray(U10).shape, np.asarray(V10).shape, np.asarray(WSPD).shape)
    print("LON/LAT shapes:", np.asarray(LON).shape, np.asarray(LAT).shape)

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
    # Colormap (you'll add wind10m in dicts.py)
    # -------------------------
    try:
        cmap, norm, bnds, cbarticks, cbarlbl, cmap_units = dicts.cmaps("wind10m")
    except Exception as e:
        print(f"NOTE: dicts.cmaps('wind10m') not found; falling back to 'ivt': {e}")
        cmap, norm, bnds, cbarticks, cbarlbl, cmap_units = dicts.cmaps("ivt")

    cbarlbl = f"10-m Wind Speed ({wspd_units})"

    # -------------------------
    # Shaded wind speed
    # -------------------------
    WSPD_plot = np.ma.masked_less(WSPD, 5.0)
    cf = ax.contourf(
        LON, LAT, WSPD_plot,
        cmap=cmap, norm=norm, levels=bnds,
        alpha=0.8, extend="max",
        transform=datacrs, zorder=10
    )

    # Optional: thin contour lines of wind speed (often looks nice)
    try:
        ax.contour(LON, LAT, WSPD, levels=bnds, colors="k",
                   linewidths=0.25, alpha=0.35, transform=datacrs, zorder=11)
    except Exception:
        pass

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
    cb.outline.set_edgecolor("black")
    cb.outline.set_linewidth(0.5)
    for col in cb.ax.collections:
        col.set_edgecolor("black")
        col.set_linewidth(0.3)

    # -------------------------
    # Wind barbs (always plotted)
    # -------------------------
    try:
        # Convert to knots for barbs
        Ukn = util.wind_ms_to_knots(U10)
        Vkn = util.wind_ms_to_knots(V10)

        s = max(1, int(args.quiver_stride or 10))

        bkw = util.default_barb_kwargs(args.model, scaling_factor=scaling_factor)
        bkw.update(dict(barbcolor="k", flagcolor="k", linewidth=0.8))

        ax.barbs(
            LON[::s, ::s], LAT[::s, ::s],
            Ukn[::s, ::s], Vkn[::s, ::s],
            transform=datacrs, zorder=200, **bkw
        )
    except Exception as e:
        print(f"NOTE: skipping wind barbs (could not plot): {e}")

    # -------------------------
    # SLP contours if available (MSLET preferred, PRMSL fallback)
    # -------------------------
    try:
        SLP_hpa, (LONs, LATs), slp_name = util.fetch_mslp(gf)  # hPa
        if args.slp_contours:
            levs = args.slp_contours
        else:
            levs = np.arange(args.slp_contour_min,
                             args.slp_contour_max + 0.001,
                             args.slp_contour_interval)

        cs = ax.contour(
            LONs, LATs, SLP_hpa,
            levels=levs,
            colors="black", linewidths=0.8, alpha=0.9,
            transform=datacrs, zorder=150
        )
        ax.clabel(cs, fmt="%d", inline=True, inline_spacing=2, fontsize=9)
        print(f"SLP contours drawn from {slp_name}")
    except Exception as e:
        print(f"NOTE: skipping SLP contours (not available): {e}")

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
    var_info = {"10-m Wind Speed": {"units": wspd_units, "feature": "shaded"}}
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

