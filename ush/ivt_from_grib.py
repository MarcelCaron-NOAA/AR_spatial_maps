#!/usr/bin/env python3
import os, sys, argparse, math
import numpy as np
import grib2io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.colors import ListedColormap
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from datetime import datetime
import pickle
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
    ap.add_argument("--var", default="IVT")
    ap.add_argument("--domain", default="CONUS_West")

    ap.add_argument("--out", required=False)         # make optional to allow --comout path
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--quiver-stride", type=int, default=10)
    ap.add_argument("--slp-contours", default="")
    ap.add_argument("--bool_analysis", default="")

    ap.add_argument("--home", default=os.getenv("HOME", ""))
    ap.add_argument("--comout", default=None)
    ap.add_argument("--fix", default="")
    ap.add_argument("--sfc-file", default=None,
                help="Optional surface GRIB2 file to read SLP (PRMSL/MSLET) from.")

    args = ap.parse_args()

    # normalize outdir
    outdir = args.out or args.comout
    if not outdir:
        ap.error("One of --out or --comout is required")
    # ensure directory exists
    os.makedirs(outdir, exist_ok=True)

    # ... build your output filename using outdir ...
    # outfile = os.path.join(outdir, f"{args.model}_{args.var}_{...}.png")

    # Open GRIB once for inventories
    gf = grib2io.open(args.file, mode='r')
    
    # Compute IVT from 1000→200 mb (tweak as needed)
    IVTu, IVTv, IVT, (LON, LAT) = util.compute_ivt(gf, pmin_mb=1000, pmax_mb=200)

    # Optional SLP for contours
    SLP = None
    slp_units = None
    try:
        if args.sfc_file:
            gf_sfc = grib2io.open(args.sfc_file, mode="r")
            SLP, slp_units = util.fetch_slp(gf_sfc)
            gf_sfc.close()
        else:
            SLP, slp_units = util.fetch_slp(gf)
    except Exception:
        pass

    # Plotting
    domain_info = dicts.domains(args.domain)
    extent = domain_info['extent']
    dx = domain_info['xticks']
    dy = domain_info['yticks']
    mapcrs = domain_info['ccrs']
    figsize = domain_info['figsize']
    
    current_dpi = 100
    base_dpi = 100
    scaling_factor = (current_dpi / base_dpi)**0.1
    util.set_params(current_dpi, scaling_factor)

    datacrs = ccrs.PlateCarree()
    fig = plt.figure(figsize=figsize)
    ax = plt.axes(projection=mapcrs)
    ax = util.draw_basemap(
        ax, extent=extent, xticks=dx, yticks=dy, left_lats=True, 
        right_lats=False, grid=True, scaling_factor=scaling_factor
    )
    cmap, norm, bnds, cbarticks, cbarlbl, ivt_units = dicts.cmaps("ivt")
   
    if isinstance(cmap, ListedColormap) and len(cmap.colors) == len(bnds):
        core_colors = cmap.colors[:-1]
        over_color = cmap.colors[-1]

        cmap = ListedColormap(core_colors, name=f"{cmap.name}_core")
        cmap.set_over(over_color)
        norm = mcolors.BoundaryNorm(bnds, ncolors=len(core_colors), clip=False)
        extend_kw = 'max'
    else:
        extend_kw = 'neither'

    # Shade IVT magnitude
    if LON is not None and LAT is not None:
        cf = ax.contourf(
            LON, LAT, IVT, transform=datacrs, levels=bnds, 
            cmap=cmap, norm=norm, alpha=0.9, extend=extend_kw, 
            zorder=100
        )
        cs_ivt = ax.contour(
            LON, LAT, IVT, transform=datacrs, levels=bnds, 
            colors='black', linewidths=0.3, alpha=0.9, zorder=100
        )

        bbox = ax.get_position()
        pad = 0.015
        width = 0.02
        cax = fig.add_axes([bbox.x1 + pad, bbox.y0, width, bbox.height])
        cb = plt.colorbar(
            cf, cax=cax, orientation='vertical', ticklocation='right', 
            ticks=cbarticks, drawedges=True, extendfrac=0.03
        )
        cb.outline.set_edgecolor('black')
        cb.outline.set_linewidth(0.5)
        for e in cb.ax.collections:
            e.set_edgecolor('black')
            e.set_linewidth(0.3)

    # Quivers (thin out via stride)
    qs = util.choose_quiver_stride(
        args.model, 
        user_qs=getattr(args, "quiver_stride", None)
    )
    if LON is not None and LAT is not None:
        LONq = LON[::qs, ::qs]; LATq = LAT[::qs, ::qs]
        Uq = IVTu[::qs, ::qs]; Vq = IVTv[::qs, ::qs]
        IVTq = IVT[::qs, ::qs]
        mask = IVTq < np.min(bnds)
        Uq = np.where(mask, np.nan, Uq)
        Vq = np.where(mask, np.nan, Vq)
        quiver = ax.quiver(
            LONq, LATq, Uq, Vq, transform=datacrs, 
            scale=200, scale_units="xy", width=0.0025, headwidth=5., 
            alpha=1., zorder=110
        )
        util.add_quiver_key_box(
            ax, quiver, ref=500, loc="upper right", boxsize=(0.1, 0.085), 
            pad=0.0, alpha=0.85
        )
    # SLP contours if available
    if SLP is not None and args.slp_contours != "":
        try:
            lo, hi, step = (int(x) for x in args.slp_contours.split(","))
            slp_levels = np.arange(lo, hi+0.1, step)
        except Exception:
            slp_levels = np.arange(900, 1050, 4)
        cs_slp = ax.contour(LON, LAT, SLP, levels=slp_levels,
            colors="k", linewidths=0.6, transform=datacrs, zorder=101
        )
        for coll in cs_slp.collections:
            coll.set_zorder(101)
        base = plt.rcParams['font.size']
        ax.clabel(cs_slp, fmt="%.0f", fontsize=base * .9)
        clabels = cs_slp.labelTexts
        if isinstance(clabels, list) and len(clabels) > 0:
            for txt in clabels:
                txt.set_zorder(101)
                txt.set_bbox(dict(facecolor='white', alpha=0.5, edgecolor='none', pad=1))

    # Logo
    logo_path = os.path.join(args.fix, "noaa.png")
    util.add_corner_logo(ax, logo_path, loc="upper left", frac=0.06, alpha=0.5)

    # Finish up
    var_info = {
        'IVT': {
            'units': ivt_units,
            'feature': 'shaded'
        },
        'IVT Vector': {
            'units': '',
            'feature': ''
        },
        'Mean SLP': {
            'units': 'hPa',
            'feature': 'contours'
        }
    }

    var_string = util.get_var_string(var_info)

    iday, vday = (args.idate[6:], args.vdate[6:])
    imonth, vmonth = (
        datetime.strptime(args.idate,'%Y%m%d').strftime('%B'), 
        datetime.strptime(args.vdate,'%Y%m%d').strftime('%B')
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
    plt.savefig(ofp, bbox_inches="tight")
    plt.close(fig)
    os.chmod(ofp, 0o755)

    print(f"New image created: {ofp}")

if __name__ == "__main__":
    main()

