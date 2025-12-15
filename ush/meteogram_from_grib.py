#!/usr/bin/env python3
import os, argparse
import numpy as np
import grib2io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from datetime import datetime, timedelta

import plot_util as util
import dicts


def parse_fhrs(s):
    # e.g. "0,3,6,9,12" or "0-72:3"
    s = s.strip()
    if "-" in s and ":" in s:
        # "start-end:step"
        rng, step = s.split(":")
        a, b = rng.split("-")
        return list(range(int(a), int(b) + 1, int(step)))
    return [int(x) for x in s.split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--date-type", required=True, choices=["INIT", "VALID"])
    ap.add_argument("--idate", required=True)   # YYYYmmdd
    ap.add_argument("--ihour", required=True)   # HH
    ap.add_argument("--vdate", required=True)   # YYYYmmdd
    ap.add_argument("--vhour", required=True)   # HH
    ap.add_argument("--fhrs", required=True, help="e.g. 0-72:3 or 0,6,12,18")
    ap.add_argument("--file-template", required=True,
                    help="Template with {FHR3} substituted (and anything else you want).")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)  # degrees east or west ok (we normalize)
    ap.add_argument("--out", required=False)
    ap.add_argument("--comout", required=False)
    ap.add_argument("--fix", default="")
    ap.add_argument("--title", default="")  # optional override
    ap.add_argument("--home", default=None)
    ap.add_argument("--tmp", default=None)

    args = ap.parse_args()
    outdir = args.out or args.comout
    if not outdir:
        ap.error("One of --out or --comout is required")
    os.makedirs(outdir, exist_ok=True)

    fhrs = parse_fhrs(args.fhrs)

    # Build time axis from INIT + fhr (use your passed INIT as truth)
    init_dt = datetime.strptime(args.idate + args.ihour, "%Y%m%d%H")
    valid_dts = [init_dt + timedelta(hours=int(f)) for f in fhrs]

    # Storage
    ivt = []
    iwv = []
    apcp = []
    rh_prof_list = []
    p_levels_hpa = None

    # Loop files
    for f in fhrs:
        f3 = f"{int(f):03d}"
        fpath = args.file_template.replace("{FHR3}", f3)
        if not os.path.exists(fpath):
            raise FileNotFoundError(f"Missing GRIB: {fpath}")

        gf = grib2io.open(fpath, mode="r")

        # ---- RH time-height (isobaric) ----
        rh_by = util.read_msgs_by_name_and_level(gf, "RH")  # dict: "850 mb" -> 2D
        # keep only pressure levels we can parse
        lev_pairs = []
        for lbl in rh_by.keys():
            p = util._parse_mb(lbl)  # you already have this pattern in your codebase
            if p is not None:
                lev_pairs.append((lbl, p))
        lev_pairs.sort(key=lambda t: t[1], reverse=True)  # 1000..200

        if p_levels_hpa is None:
            p_levels_hpa = np.array([p for _, p in lev_pairs], dtype=float)

        # point sample RH at each level
        # use a representative message for grid mapping
        ref_lbl = lev_pairs[0][0]
        ref_msg = util.read_msgs_by_name_and_level(gf, "RH", return_msgs=True)[ref_lbl]
        j, i = util.ij_from_latlon_regular_ll(ref_msg, args.lat, args.lon)

        rh_prof = np.array([rh_by[lbl][j, i] for lbl, _p in lev_pairs], dtype=float)
        rh_prof_list.append(rh_prof)

        # ---- IWV + IVT point values ----
        # Use your proven “dp>0” approach but do it at a point
        ivt_val, iwv_val = util.compute_ivt_iwv_point(gf, j, i, pmin_mb=1000, pmax_mb=200)
        ivt.append(ivt_val)
        iwv.append(iwv_val)

        # ---- Precip (start simple: accumulated APCP at surface) ----
        # Later you can compute increments / rates by differencing.
        apcp_val = util.fetch_apcp_surface_point(gf, j, i)
        apcp.append(apcp_val)

        gf.close()

    rh_timeheight = np.vstack(rh_prof_list)  # (T, L)
    ivt = np.array(ivt, dtype=float)
    iwv = np.array(iwv, dtype=float)
    apcp = np.array(apcp, dtype=float)

    # ===== Plot =====
    fig = plt.figure(figsize=(10, 10), dpi=150)
    gs = GridSpec(nrows=3, ncols=1, height_ratios=[3.0, 1.4, 1.8], hspace=0.22)

    # ---- Panel 1: RH time-height ----
    ax1 = fig.add_subplot(gs[0])
    T = mdates.date2num(valid_dts)

    # RH colormap: use your dicts if you have it; fallback if not.
    try:
        cmap, norm, bnds, ticks, lbl, _units = dicts.cmaps("rh")
        levels = bnds
    except Exception:
        cmap = "YlGn"
        levels = np.arange(10, 101, 10)

    # Need meshgrid in (time, pressure)
    TT, PP = np.meshgrid(T, p_levels_hpa)
    # contourf expects (ny,nx); we have rh (T,L) so transpose to (L,T)
    cf = ax1.contourf(TT, PP, rh_timeheight.T, levels=levels, cmap=cmap, extend="max")

    ax1.set_yscale("log")
    ax1.invert_yaxis()
    ax1.set_ylabel("Pressure (hPa)")
    ax1.set_ylim(np.nanmax(p_levels_hpa), np.nanmin(p_levels_hpa))

    # Pressure ticks: pick a standard set and only show those in range
    stdp = np.array([1000, 925, 850, 700, 500, 300, 250, 200], dtype=float)
    stdp = stdp[(stdp <= np.nanmax(p_levels_hpa)) & (stdp >= np.nanmin(p_levels_hpa))]
    ax1.set_yticks(stdp)
    ax1.get_yaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax1.set_yticklabels([f"{int(p)}" for p in stdp])

    cbar = fig.colorbar(cf, ax=ax1, orientation="horizontal", pad=0.08, fraction=0.08)
    cbar.set_label("RH (%)")

    # ---- Panel 2: Precip ----
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax2.plot(valid_dts, apcp, linewidth=1.5)
    ax2.set_ylabel("APCP (mm)")
    ax2.grid(True, alpha=0.25)
    ax2.set_title(f"Total Precip = {float(np.nanmax(apcp)):.2f} mm", loc="right")

    # ---- Panel 3: IVT + IWV ----
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax3.plot(valid_dts, iwv, linewidth=1.8)  # IWV left
    ax3.set_ylabel("IWV (mm)")
    ax3.grid(True, alpha=0.25)

    ax3r = ax3.twinx()
    ax3r.plot(valid_dts, ivt, linewidth=1.5)  # IVT right
    ax3r.set_ylabel(r"IVT (kg m$^{-1}$ s$^{-1}$)")

    ax3.set_title(f"Max IVT/IWV = {np.nanmax(ivt):.0f} / {np.nanmax(iwv):.0f}", loc="left")

    # ---- Time axis formatting ----
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H%M"))
    ax3.xaxis.set_major_locator(mdates.HourLocator(interval=max(6, int((len(valid_dts) / 10) * 6))))
    plt.setp(ax1.get_xticklabels(), visible=False)
    plt.setp(ax2.get_xticklabels(), visible=False)

    # Main title
    lat = args.lat
    lon = util.normalize_lon_180(args.lon)
    title = args.title.strip() if args.title.strip() else f"{args.model.upper()} Meteogram | {lat:.2f}N {abs(lon):.2f}{'W' if lon < 0 else 'E'}"
    fig.suptitle(title + "\n" + f"Initialized: {args.ihour}Z {args.idate} | Valid start: {valid_dts[0].strftime('%HZ %Y%m%d')}", y=0.98)

    # Output
    datedir = os.path.join(outdir, args.idate)
    os.makedirs(datedir, exist_ok=True)
    ofn = os.path.join(datedir, f"meteogram.{args.model}.{args.idate}{args.ihour}.{lat:.2f}_{lon:.2f}.png")
    fig.savefig(ofn, bbox_inches="tight")
    plt.close(fig)
    print(f"New image created: {ofn}")


if __name__ == "__main__":
    main()

