#!/usr/bin/env python3
import os, argparse
import numpy as np
import grib2io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import ScalarFormatter, FixedLocator, FuncFormatter, NullLocator, NullFormatter
from datetime import datetime, timedelta

import plot_util as util
import dicts

# TEMP
import pickle

def _is_dt_like(x):
    return isinstance(x, (datetime, np.datetime64))

def _fmt_xlim(xlim):
    a, b = xlim
    # try interpret as matplotlib date numbers
    try:
        da = mdates.num2date(a)
        db = mdates.num2date(b)
        return f"{xlim}  (as dates: {da} .. {db})"
    except Exception:
        return f"{xlim}"
# TEMP


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
    ap.add_argument("--models", required=True, help="Comma list: arafs,gfsv16,aigfsv1")
    ap.add_argument("--date-type", required=True, choices=["INIT", "VALID"])
    ap.add_argument("--idate", required=True)   # YYYYmmdd
    ap.add_argument("--ihour", required=True)   # HH
    ap.add_argument("--fhrs", required=True, help="e.g. 0-72:3 or 0,6,12,18")

    # Per-model file templates (must contain {FHR3})
    ap.add_argument("--template-arafs", default="")
    ap.add_argument("--template-gfsv16", default="")
    ap.add_argument("--template-gfsv17", default="")
    ap.add_argument("--template-gdas", default="")
    ap.add_argument("--template-aigfsv1-pres", default="")
    ap.add_argument("--template-aigfsv1-sfc", default="")   # optional (APCP/SLP live here sometimes)

    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--out", required=False)
    ap.add_argument("--comout", required=False)
    ap.add_argument("--fix", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--home", default=os.getenv("HOME", ""))

    args = ap.parse_args()
    models = [m.strip().lower() for m in args.models.split(",") if m.strip()]
    if not models:
        raise ValueError("No models provided to --models")
    if len(models) > 3:
        print("[crosssection] More than 3 models requested; plotting only first 3 RH panels.")
    models_rh = models[:3]   # RH panels max

    tmpl = {}

    if args.template_arafs:  tmpl["arafs"]  = {"main": args.template_arafs}
    if args.template_gfsv16: tmpl["gfsv16"] = {"main": args.template_gfsv16}
    if args.template_gfsv17: tmpl["gfsv17"] = {"main": args.template_gfsv17}
    if args.template_gdas:   tmpl["gdas"]   = {"main": args.template_gdas}

    if args.template_aigfsv1_pres:
        tmpl["aigfsv1"] = {"main": args.template_aigfsv1_pres}
        if args.template_aigfsv1_sfc:
            tmpl["aigfsv1"]["sfc"] = args.template_aigfsv1_sfc

    for model in models:
        if model not in tmpl:
            raise ValueError(f"No template provided for model '{model}'")
    
    outdir = args.out or args.comout
    if not outdir:
        ap.error("One of --out or --comout is required")
    os.makedirs(outdir, exist_ok=True)

    fhrs = parse_fhrs(args.fhrs)
    init_dt = datetime.strptime(args.idate + args.ihour, "%Y%m%d%H")
    valid_dts = [init_dt + timedelta(hours=int(f)) for f in fhrs]
    
    print("len(valid_dts)=", len(valid_dts))
    print("valid_dts[0],[-1]=", valid_dts[0], valid_dts[-1])
    print("type(valid_dts[0])=", type(valid_dts[0]))
    print("fhrs[:10]=", fhrs[:10], " ... last=", fhrs[-1], "len=", len(fhrs))

    # Choose a consistent lon convention for sampling
    lon_samp = util.normalize_lon_180(args.lon)

    # First pass: determine a common set of RH pressure levels across the models we will RH-plot
    common_p = None
    ij_by_model = {}

    for model in models_rh:
        f0 = f"{int(fhrs[0]):03d}"
        fpath0 = tmpl[model]["main"].replace("{FHR3}", f0)
        gf0 = grib2io.open(fpath0, mode="r")

        rh_by = util.read_msgs_by_name_and_level(gf0, "RH")  # {"850 mb": 2D, ...}
        lev_pairs = []
        for lbl in rh_by.keys():
            p = util._parse_mb(lbl)
            if p is not None:
                lev_pairs.append((lbl, p))
        if not lev_pairs:
            raise RuntimeError(f"{model}: No RH pressure levels found.")

        # Build set of pressures for this model
        pset = set([p for _lbl, p in lev_pairs])

        if common_p is None:
            common_p = pset
        else:
            common_p = common_p.intersection(pset)

        # Compute sampling i,j for THIS model (grid may differ by model)
        # Pick one RH message as reference
        ref_lbl = lev_pairs[0][0]
        ref_msg = util.read_msgs_by_name_and_level(gf0, "RH", return_msgs=True)[ref_lbl]
        j, i = util.ij_from_latlon_regular_ll(ref_msg, args.lat, lon_samp)
        ij_by_model[model] = (j, i)

        gf0.close()

    if not common_p:
        raise RuntimeError("No common RH pressure levels across models. (intersection empty)")

    p_levels_hpa = np.array(sorted(list(common_p), reverse=True), dtype=float)  # 1000..200-ish


    # Now the real loop
    series = {}
    for model in models:
        ivt = []; iwv = []; apcp = []
        rh_prof_list = []  # only if model in models_rh
        u_prof_list = []
        v_prof_list = []
        tmp_prof_list = []

        # get (j,i) if we precomputed it; otherwise compute on first file
        ji = ij_by_model.get(model, None)

        for f in fhrs:
            f3 = f"{int(f):03d}"
            fpath = tmpl[model]["main"].replace("{FHR3}", f3)
            if not os.path.exists(fpath):
                raise FileNotFoundError(f"Missing GRIB: {fpath}")

            gf = grib2io.open(fpath, mode="r")

            """
            # TEMP FOR DEBUGGING ONLY 
            valid_expected = init_dt + timedelta(hours=int(f))
            rh_msgs = util.read_msgs_by_name_and_level(gf, "RH", return_msgs=True)
            any_msg = next(iter(rh_msgs.values()))
            vt = getattr(any_msg, "validDate", None)
            if vt is None:
                vt = getattrs(any_msg, "valid_datetime", None)

            print(f"[TIMECHK] fhr={f3}  expected={valid_expected}  grib_valid={vt}  file={os.path.basename(fpath)}")
            """

            # compute ij once if needed
            if ji is None:
                rh_msgs = util.read_msgs_by_name_and_level(gf, "RH", return_msgs=True)
                if not rh_msgs:
                    raise RuntimeError(f"{model}: need RH to compute ij, but RH not found.")
                ref_lbl = list(rh_msgs.keys())[0]
                ref_msg = rh_msgs[ref_lbl]
                ji = util.ij_from_latlon_regular_ll(ref_msg, args.lat, lon_samp)
            j, i = ji

            if model in models_rh:
                # --- RH ---
                rh_by = util.read_msgs_by_name_and_level(gf, "RH")
                rh_p = {}
                for lbl, arr in rh_by.items():
                    p = util._parse_mb(lbl)
                    if p is not None:
                        rh_p[p] = float(arr[j, i])
                rh_prof = np.array([rh_p.get(p, np.nan) for p in p_levels_hpa], dtype=float)
                rh_prof_list.append(rh_prof)

                # --- Winds (UGRD/VGRD) on isobaric levels ---
                u_by = util.read_msgs_by_name_and_level(gf, "UGRD")
                v_by = util.read_msgs_by_name_and_level(gf, "VGRD")
                u_p, v_p = {}, {}
                for lbl, arr in u_by.items():
                    p = util._parse_mb(lbl)
                    if p is not None:
                        u_p[p] = float(arr[j, i])
                for lbl, arr in v_by.items():
                    p = util._parse_mb(lbl)
                    if p is not None:
                        v_p[p] = float(arr[j, i])

                u_prof = np.array([u_p.get(p, np.nan) for p in p_levels_hpa], dtype=float)
                v_prof = np.array([v_p.get(p, np.nan) for p in p_levels_hpa], dtype=float)

                # Convert m/s -> knots for barbs (optional but typical)
                u_prof = util.wind_ms_to_knots(u_prof)
                v_prof = util.wind_ms_to_knots(v_prof)

                u_prof_list.append(u_prof)
                v_prof_list.append(v_prof)

                # --- Temperature (TMP) on isobaric levels for freezing line ---
                tmp_by = util.read_msgs_by_name_and_level(gf, "TMP")  # usually Kelvin in GRIB
                t_p = {}
                for lbl, arr in tmp_by.items():
                    p = util._parse_mb(lbl)
                    if p is not None:
                        t_p[p] = float(arr[j, i])

                tmp_prof = np.array([t_p.get(p, np.nan) for p in p_levels_hpa], dtype=float)
                tmp_prof_list.append(tmp_prof)


            '''
            # RH time-height (only for models_rh)
            if model in models_rh:
                rh_by = util.read_msgs_by_name_and_level(gf, "RH")  # "850 mb" -> 2D
                # build p->value dict at point
                rh_p = {}
                for lbl, arr in rh_by.items():
                    p = util._parse_mb(lbl)
                    if p is not None:
                        rh_p[p] = float(arr[j, i])

                # vector on common pressure grid (missing -> nan)
                rh_prof = np.array([rh_p.get(p, np.nan) for p in p_levels_hpa], dtype=float)
                rh_prof_list.append(rh_prof)
            '''

            # IVT/IWV point values (from main file)
            ivt_val, iwv_val = util.compute_ivt_iwv_point(gf, j, i, pmin_mb=1000, pmax_mb=200)
            ivt.append(ivt_val); iwv.append(iwv_val)

            # APCP: if model has a separate sfc file, read from it
            if "sfc" in tmpl.get(model, {}):
                sfc_path = tmpl[model]["sfc"].replace("{FHR3}", f3)
                gf_sfc = grib2io.open(sfc_path, mode="r")
                apcp_val = util.fetch_apcp_surface_point(gf_sfc, j, i)
                gf_sfc.close()
            else:
                apcp_val = util.fetch_apcp_surface_point(gf, j, i)
            apcp.append(apcp_val)

            gf.close()

        series[model] = {
            "ivt": np.array(ivt, float),
            "iwv": np.array(iwv, float),
            "apcp": np.array(apcp, float),
        }
        if model in models_rh:
            series[model]["rh"] = np.vstack(rh_prof_list)  # (T,L)
            series[model]["u"] = np.vstack(u_prof_list)  # (T,L) knots
            series[model]["v"] = np.vstack(v_prof_list)  # (T,L) knots
            series[model]["tmp"] = np.vstack(tmp_prof_list)  # (T,L) Kelvin

    # ===== Plot =====
    n_rh = len(models_rh)
    fig = plt.figure(figsize=(10, 3.4*n_rh + 4.0), dpi=150)

    # RH panels + precip + IVT/IWV
    gs = GridSpec(
        nrows=n_rh + 2, ncols=1,
        height_ratios=[3.0]*n_rh + [1.2, 1.6],
        hspace=0.22
    )

    with open('/lfs/h2/emc/vpppg/noscrub/marcel.caron/test.pkl','wb') as f:
        pickle.dump([valid_dts, p_levels_hpa], f)

    T = mdates.date2num(valid_dts)
    TT, PP = np.meshgrid(T, p_levels_hpa)

    print("T min/max:", np.min(T), np.max(T), mdates.num2date(np.min(T)), mdates.num2date(np.max(T)))
    print("TT min/max:", np.nanmin(TT), np.nanmax(TT), mdates.num2date(np.nanmin(TT)), mdates.num2date(np.nanmax(TT)))

    # RH colormap
    try:
        cmap, norm, bnds, ticks, lbl, _units = dicts.cmaps("rh")
        levels = bnds
    except Exception:
        cmap = "YlGn"
        norm = None
        levels = np.arange(10, 101, 10)
    #cmap.set_under("white")

    rh_axes = []
    cf_last = None

    for k, model in enumerate(models_rh):
        ax = fig.add_subplot(gs[k], sharex=rh_axes[0] if rh_axes else None)
        rh_axes.append(ax)

        rh_timeheight = series[model]["rh"]  # (T,L)
        cf = ax.contourf(
            TT, PP, rh_timeheight.T,
            levels=levels, cmap=cmap, norm=norm,
            extend="neither"
        )
        cf_last = cf
        cs = ax.contour(
            TT, PP, rh_timeheight.T,
            levels=levels,              # same bin edges
            colors="0.5",               # gray (0=black, 1=white)
            linewidths=0.35,
            alpha=0.8,
        )

        ax.set_yscale("log")
        ax.invert_yaxis()
        ax.set_ylim(1000, 200)
        ax.set_ylabel("Pressure (hPa)", fontweight="bold")
        ax.set_title(f"{model.upper()}", loc="left", fontweight="bold")

        stdp = np.array([1000, 925, 850, 700, 600, 500, 400, 300, 250, 200], dtype=float)
        ax.set_yscale("log")
        ax.invert_yaxis()
        ax.set_ylim(1000, 200)
        # Force tick locations
        ax.yaxis.set_major_locator(FixedLocator(stdp))
        # Force integer labels
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{int(y)}"))
        # Prevent “bonus” ticks/labels from appearing
        ax.yaxis.set_minor_locator(NullLocator())
        ax.yaxis.set_minor_formatter(NullFormatter())

        #if k < n_rh - 1:
        #    plt.setp(ax.get_xticklabels(), visible=False)
        plt.setp(ax.get_xticklabels(), visible=False)

        U = series[model]["u"].T
        V = series[model]["v"].T
        t_stride = max(1, int(len(valid_dts) / 18))
        p_stride = 2

        t_idx = np.arange(0, len(valid_dts), t_stride)
        p_idx = np.arange(0, len(p_levels_hpa), p_stride)

        TTb = TT[p_idx][:, t_idx]
        PPb = PP[p_idx][:, t_idx]
        Ub  = U[p_idx][:, t_idx]
        Vb  = V[p_idx][:, t_idx]

        Ub = np.ma.masked_invalid(Ub)
        Vb = np.ma.masked_invalid(Vb)

        barb_kw = util.default_barb_kwargs(model, scaling_factor=1.0)
        # "Bold" the barbs by bumping linewidth a bit
        barb_kw.update(dict(
            barbcolor="k",
            flagcolor="k",
            linewidth=0.8,
            zorder=50
        ))

        ax.barbs(TTb, PPb, Ub, Vb, **barb_kw)

        Tprof = series[model]["tmp"]
        p_freeze = util.freezing_pressure_hpa(p_levels_hpa, Tprof, t0_k=273.15)

        ax.plot(valid_dts, p_freeze, color="blue", linewidth=2.2, zorder=60)

    xmin = valid_dts[0]
    xmax = valid_dts[-1]
    rh_axes[0].set_xlim(xmin, xmax)

    # --- shared RH colorbar on RHS spanning ALL RH panels ---
    bbox_top = rh_axes[0].get_position()
    bbox_bot = rh_axes[-1].get_position()
    cax_pad = 0.015
    cax_width = 0.010  # ~half-thin vs your 0.015
    cax = fig.add_axes([
        bbox_top.x1 + cax_pad,
        bbox_bot.y0,
        cax_width,
        bbox_top.y1 - bbox_bot.y0
    ])
    cbar = fig.colorbar(cf_last, cax=cax, orientation="vertical", ticks=ticks)
    cbar.set_label("RH (%)", fontweight="bold")
    for lbl in cbar.ax.get_yticklabels():
        lbl.set_fontweight("bold")
    
    for ax in rh_axes:
        ax.tick_params(labelbottom=False)

    # ---- Panel: Precip (multi-model) ----
    axp = fig.add_subplot(gs[n_rh], sharex=rh_axes[0])
    axp.grid(True, alpha=0.25)
    axp.set_ylabel("Precip.\n(mm)", fontweight="bold")

    
    for model in models:
        col = dicts.model_colors(model)
        if min(fhrs) == 0 and series[model]["apcp"][0] != 0:
            print(f"Setting non-zero {model} F000 precipitation to zero")
            series[model]["apcp"][0] = 0
        axp.plot(valid_dts, series[model]["apcp"], linewidth=1.4, label=model.upper(), color=col)

    pleg = axp.legend(loc="upper left", ncol=min(3, len(models)), fontsize=9)
    for txt in pleg.get_texts():
        txt.set_fontweight("bold")
    plt.setp(axp.get_xticklabels(), visible=False)
    axp.tick_params(labelbottom=False)


    # ---- Panel: IVT + IWV (multi-model) ----
    ax3 = fig.add_subplot(gs[n_rh + 1], sharex=rh_axes[0])
    ax3.grid(True, alpha=0.25)
    ax3.set_ylabel("IWV\n(mm; dashed)", fontweight="bold")

    ax3r = ax3.twinx()
    ax3r.set_ylabel("IVT\n" + r"(kg m$^{-1}$ s$^{-1}$; solid)", fontweight="bold")
    #ax3r.axhline(250, color="0.5", linestyle=":", linewidth=1.0, zorder=0) # removed because it doesn't deal with the date axes well
    ax3r.plot([valid_dts[0], valid_dts[-1]], [250, 250], color="0.5", linestyle="-", linewidth=1.0, zorder=0)
    ax3.plot([valid_dts[0], valid_dts[-1]], [20, 20], color="0.5", linestyle=":", linewidth=1.0, zorder=0)
    ax3r.plot(valid_dts[-1], 250, marker="o", markersize=4, color="0.5", zorder=0, clip_on=False)
    ax3.plot(valid_dts[0], 20, marker="o", markersize=4, color="0.5", zorder=0, clip_on=False)

    edges = util._time_edges(valid_dts)

    for model in models:
        col = dicts.model_colors(model)
        
        # Shade AR events
        ar_mask = (np.asarray(series[model]['ivt']) >= 250.0) & (np.asarray(series[model]['iwv']) >= 20.0)
        ar_mask &= np.isfinite(series[model]['ivt']) & np.isfinite(series[model]['iwv'])

        for s, e in util._contiguous_true_runs(ar_mask):
            left = edges[s]
            right = edges[e + 1]
            ax3.axvspan(
                left, right, facecolor=col, alpha=0.12, linewidth=0.0, zorder=0
            )

        # Plot IWV (left) and IVT (right)
        ax3.plot(valid_dts, series[model]["iwv"], linewidth=1.4, linestyle="--", color=col)
        ax3r.plot(valid_dts, series[model]["ivt"], linewidth=1.4, linestyle="-",  color=col)

    #ax3.set_title("IVT (solid) and IWV (dashed) by model", loc="left")


    # ---- Time axis formatting ----
    tick_dts = valid_dts
    tick_locs = mdates.date2num(tick_dts)

    ax3.xaxis.set_major_locator(FixedLocator(tick_locs))
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%HZ"))

    max_labels = 13
    force_even = True
    n = len(tick_locs)
    step = int(np.ceil(n / max_labels))
    if force_even and step > 1:
        step = 2 if step <= 2 else step

    labels = [
        dt.strftime("%b %d\n%HZ") if (i % step == 0) else ""
        for i, dt in enumerate(tick_dts)
    ]
    ax3.set_xticks(tick_dts)
    ax3.set_xticklabels(labels)
    ax3.tick_params(labelbottom=True)

    for ax in [*rh_axes, axp, ax3, ax3r]:
        ax.xaxis.label.set_fontweight("bold")
        ax.yaxis.label.set_fontweight("bold")
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_fontweight("bold")

    lat = args.lat
    lon = util.normalize_lon_180(args.lon)    
    deg = "\N{DEGREE SIGN}"
    title = (
        args.title.strip() 
        if args.title.strip() 
        else f"Time-Height Meteogram | "
            f"{abs(lat):.2f}{deg}{'N' if lat >= 0 else 'S'} " 
            f"{abs(lon):.2f}{deg}{'W' if lon < 0 else 'E'}"
    )
    init_dt = datetime.strptime(args.idate + args.ihour, "%Y%m%d%H")
    init_str = init_dt.strftime("Init %HZ %d %b %Y")
    fig.suptitle(title + "\n" + init_str, y=0.93, fontweight="bold")


    # Output
    datedir = os.path.join(outdir, args.idate)
    os.makedirs(datedir, exist_ok=True)
    ofn = os.path.join(datedir, f"th_meteo.{args.idate}{args.ihour}.{lat:.2f}_{lon:.2f}.png")
    # TEMP
    print("\n=== DEBUG: shared-x + xlim + units ===")
    for k, ax in enumerate(fig.axes):
        try:
            xlim = ax.get_xlim()
        except Exception as e:
            print(f"AX{k}: get_xlim failed: {e}")
            continue

        # identify what this axis shares x with
        shared = ax.get_shared_x_axes().get_siblings(ax)
        shared_ids = [id(a) for a in shared]
        print(f"\nAX{k} id={id(ax)} shared_with={len(shared)-1} siblings_ids={shared_ids}")

        print(f"  xlim raw: {_fmt_xlim(xlim)}")

        # locator / formatter
        loc = ax.xaxis.get_major_locator()
        fmt = ax.xaxis.get_major_formatter()
        print(f"  locator: {loc.__class__.__name__}  formatter: {fmt.__class__.__name__}")

        # how many ticks does it want?
        try:
            ticks = ax.get_xticks()
            print(f"  xticks count={len(ticks)}  sample={ticks[:5]} ... {ticks[-5:] if len(ticks)>5 else ticks}")
        except Exception as e:
            print(f"  get_xticks failed: {e}")

        # examine a couple of artists for x-data type
        lines = ax.get_lines()
        if lines:
            xd = lines[0].get_xdata()
            if len(xd) > 0:
                x0 = xd[0]
                print(f"  line0 xdata type={type(x0)}  sample={x0}")
        else:
            print("  no Line2D on this axis")

        # for QuadContourSet / PolyCollection-like (contourf), best hint is xlim above,
        # but we can also dump collections count
        try:
            print(f"  collections={len(ax.collections)}")
        except Exception:
            pass

    print("\n=== DEBUG: valid_dts summary ===")
    print("len(valid_dts)=", len(valid_dts))
    print("valid_dts[0],[-1]=", valid_dts[0], valid_dts[-1], "types:", type(valid_dts[0]), type(valid_dts[-1]))
    print("mdates.date2num(valid_dts)[0],[-1]=", mdates.date2num(valid_dts[0]), mdates.date2num(valid_dts[-1]))
    for k, ax in enumerate([*rh_axes, axp, ax3, ax3r]):
        for li, line in enumerate(ax.get_lines()):
            xd = line.get_xdata()
            if len(xd):
                try:
                    xdn = mdates.date2num(xd) # will work if datetime-like
                    print(f"AX{k} line{li} date2num min/max:", np.min(xdn), np.max(xdn))
                except Exception:
                    xdn = np.asarray(xd, dtype=float)
                    print(f"AX{k} line{li} numeric min/max:", np.min(xdn), np.max(xdn))
    # TEMP
    fig.savefig(ofn, bbox_inches="tight")
    plt.close(fig)
    print(f"New image created: {ofn}")


if __name__ == "__main__":
    main()

