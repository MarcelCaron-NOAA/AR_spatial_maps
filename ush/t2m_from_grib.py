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
    ap.add_argument("--var", default="T2M")
    ap.add_argument("--domain", default="conusw")

    ap.add_argument("--out", required=False)
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--bool_analysis", default="")
    ap.add_argument("--units", default="C", choices=["K","C","F"],
                    help="Output units for 2-m temperature (default: C).")

    ap.add_argument("--home", default=os.getenv("HOME", ""))
    ap.add_argument("--comout", default=None)
    ap.add_argument("--fix", default="")
    ap.add_argument("--quiver-stride", type=int, default=10,
                    help="Stride for wind barbs (plot every Nth grid point).")

    args, unknown = ap.parse_known_args()
    if unknown:
        print(f"NOTE: ignoring unrecognized args: {' '.join(unknown)}")

    print(args.quiver_stride)

    outdir = args.out or args.comout
    if not outdir:
        ap.error("One of --out or --comout is required")
    os.makedirs(outdir, exist_ok=True)

    gf = grib2io.open(args.file, mode="r")

    # Fetch 2-m temperature
    T2m, (LON, LAT), units_out = util.fetch_tmp_2m(gf, units=args.units)

    print(np.array(T2m).shape, np.array(LON).shape, np.array(LAT).shape)

    # Plot settings
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
    ax.set_extent(extent, crs=proj)
    util.draw_basemap(
        ax, datacrs=datacrs, extent=extent, xticks=dx, yticks=dy, 
        left_lats=True, right_lats=False, grid=True, 
        scaling_factor=scaling_factor
    )

    # Colormap
    cmap, norm, bnds, cbarticks, cbarlbl, cmap_units = dicts.cmaps("t2m")
    # If user requested F or K, keep same cmap but fix label/units
    if units_out != "°C":
        cbarlbl = f"2-m Temperature ({units_out})"

    # Shaded field
    cf = ax.contourf(
        LON, LAT, T2m, cmap=cmap, norm=norm, levels=bnds, 
        alpha=0.8, extend="both", transform=datacrs, zorder=10
    )
    cs_t2m = ax.contour(
        LON, LAT, T2m, transform=datacrs, levels=bnds,
        colors='black', linewidths=0.3, alpha=0.9, zorder=11
    )
    cs_0C = ax.contour(
        LON, LAT, T2m, transform=datacrs, levels=[0],
        colors='red', linewidths=0.7, alpha=0.9, zorder=12
    )

    bbox=ax.get_position()
    pad = 0.015
    width = 0.02
    cax = fig.add_axes([bbox.x1 + pad, bbox.y0, width, bbox.height])
    cb = plt.colorbar(cf, cax=cax, orientation="vertical", ticklocation='right',
                      extendfrac=0.03, ticks=cbarticks, drawedges=True)
    cb.outline.set_edgecolor('black')
    cb.outline.set_linewidth(0.5)
    for e in cb.ax.collections:
        e.set_edgecolor('black')
        e.set_linewidth(0.3)

    # Wind barbs
    try:
        # --- fetch 10-m winds ---
        u_by = util.read_msgs_by_name_and_level(gf, "UGRD", return_msgs=False)
        v_by = util.read_msgs_by_name_and_level(gf, "VGRD", return_msgs=False)

        lvl = "10 m above ground"
        if lvl not in u_by or lvl not in v_by:
            # tolerate alternate label variants
            cand_u = [k for k in u_by.keys() if "10 m" in k and "ground" in k]
            cand_v = [k for k in v_by.keys() if "10 m" in k and "ground" in k]
            if not cand_u or not cand_v:
                raise RuntimeError("10-m winds (UGRD/VGRD at 10 m above ground) not found.")
            lvl = cand_u[0]

        U = u_by[lvl]
        V = v_by[lvl]

        # Convert to knots (your util has this)
        Ukn = util.wind_ms_to_knots(U)
        Vkn = util.wind_ms_to_knots(V)

        # --- stride / thinning ---
        s = max(1, int(getattr(args, "quiver_stride", 10)))
        print(s)

        # Use the SAME lon/lat grid you plotted T2m on
        LONb = LON[::s, ::s]
        LATb = LAT[::s, ::s]
        Uknb = Ukn[::s, ::s]
        Vknb = Vkn[::s, ::s]

        # Barb styling 
        bkw = util.default_barb_kwargs(args.model, scaling_factor=scaling_factor)
        bkw.update(dict(
            barbcolor="k",
            flagcolor="k",
            linewidth=0.8,
        ))

        ax.barbs(
            LONb, LATb, Uknb, Vknb,
            transform=datacrs,
            zorder=200,
            **bkw
        )
    except Exception as e:
        print(f"NOTE: skipping wind barbs (could not plot): {e}")


    # Optional logo
    if args.fix:
        logo_path = os.path.join(args.fix, "noaa.png")
        if os.path.exists(logo_path):
            util.add_corner_logo(ax, logo_path, loc="upper left", frac=0.06, alpha=0.5)

    # Titles
    var_info = {
        '2-m Temperature': {'units': units_out, 'feature': 'shaded'}
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
    default_fs = matplotlib.rcParams['axes.titlesize']
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
