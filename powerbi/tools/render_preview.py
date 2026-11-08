"""Render a static preview of each Power BI report page from the real pipeline data.

Reads the same reporting.* views the Power BI model imports and computes each KPI with the
same logic as the DAX measures in _Measures.tmdl (named in comments). The output is a
preview of what the report shows, drawn with matplotlib, not a Power BI screenshot.

Usage (after `python -m fleethealth backfill`):
    python powerbi/tools/render_preview.py          # writes powerbi/preview/*.png
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from fleethealth import db  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "preview"
INK, MUTED, GRID, PANEL, BG = "#1F2933", "#616E7C", "#E4E7EB", "#FFFFFF", "#F3F4F6"
AMBER, BLUE, GREEN, RED, PURPLE = "#F2A93B", "#2E6F9E", "#3C9D5D", "#D64545", "#7B5EA7"
ALERT_COLOURS = {"no_generation": RED, "underperforming": AMBER, "missing_data": "#8C8C8C"}
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.titleweight": "bold"})


def load():
    with db.connect() as c:
        def q(sql):
            cur = c.execute(sql)
            return pd.DataFrame(cur.fetchall(), columns=[col.name for col in cur.description])
        return dict(site=q("SELECT * FROM reporting.dim_site"),
                    daily=q("SELECT * FROM reporting.fact_daily"),
                    alerts=q("SELECT * FROM reporting.fact_alerts"),
                    runs=q("SELECT * FROM reporting.pipeline_runs ORDER BY run_id"),
                    dates=q("SELECT * FROM reporting.dim_date"))


# ---------- measure logic (mirrors _Measures.tmdl) ----------
def measures(d):
    daily, alerts = d["daily"], d["alerts"]
    expected = len(d["site"]) * len(d["dates"])                              # [Expected Site Days]
    outage = daily["is_outage_day"].sum()                                    # [Outage Days]
    missing = (alerts["alert_type"] == "missing_data").sum()                 # [Missing Data Days]
    runs = d["runs"]
    return {
        "Fleet Uptime %": 1 - (outage + missing) / expected,                 # [Fleet Uptime %]
        "Total Generation kWh": daily["gen_kwh"].sum(),
        "Critical Alerts": (alerts["severity"] == "critical").sum(),
        "Alerts per 1,000 Site-Days": len(alerts) / expected * 1000,
        "Data Completeness %": daily["gen_intervals"].sum() / (expected * 48),
        "Quarantine Rate %": runs["rows_quarantined"].sum() / max(runs["rows_in"].sum(), 1),
        "Last Run Status": runs["status"].iloc[-1] if len(runs) else "n/a",
        "Last Run Finished": runs["finished_at"].iloc[-1] if len(runs) else None,
        "Rows Quarantined": runs["rows_quarantined"].sum(),
    }


# ---------- drawing helpers ----------
def canvas(title, subtitle):
    fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
    fig.text(0.02, 0.955, title, fontsize=20, weight="bold", color=INK)
    fig.text(0.02, 0.925, subtitle, fontsize=10, color=MUTED)
    fig.text(0.98, 0.012, "Preview rendered from the reporting views with the report's measure logic. Not a Power BI screenshot.",
             ha="right", fontsize=8, color="#9AA5B1")
    return fig


def panel(fig, rect, title=None):
    x, y, w, h = rect
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.006",
                                      transform=fig.transFigure, fc=PANEL, ec=GRID, lw=1, zorder=-2))
    if title:
        fig.text(x + 0.01, y + h - 0.03, title, fontsize=11, weight="bold", color=INK)
    return fig.add_axes([x + 0.035, y + 0.06, w - 0.05, h - 0.12])


def kpi(fig, rect, label, value, colour=INK, note=None):
    x, y, w, h = rect
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.006",
                                      transform=fig.transFigure, fc=PANEL, ec=GRID, lw=1, zorder=-2))
    fig.text(x + 0.012, y + h - 0.035, label, fontsize=10, color=MUTED)
    fig.text(x + 0.012, y + 0.035 + (0.02 if note else 0), value, fontsize=26, weight="bold", color=colour)
    if note:
        fig.text(x + 0.012, y + 0.018, note, fontsize=8, color=MUTED)


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def slicer_strip(fig, items):
    x = 0.55
    for label, value in items:
        fig.patches.append(FancyBboxPatch((x, 0.915), 0.135, 0.055, boxstyle="round,pad=0,rounding_size=0.005",
                                          transform=fig.transFigure, fc="#ECE6F5", ec=GRID))
        fig.text(x + 0.008, 0.952, label, fontsize=8, color=MUTED)
        fig.text(x + 0.008, 0.925, value, fontsize=9.5, color=INK, weight="bold")
        x += 0.145


def fmt_int(v):
    return f"{v:,.0f}"


# ---------- pages ----------
def fleet_overview(d, m):
    dates = d["dates"]
    fig = canvas("Solar fleet health: fleet overview", f"{len(d['site'])} rooftop systems, Ausgrid Solar Home data, "
                 f"{dates['date'].min():%d %b %Y} to {dates['date'].max():%d %b %Y}")
    slicer_strip(fig, [("Date range", f"{dates['date'].min():%b %Y} – {dates['date'].max():%b %Y}"),
                       ("Season", "All"), ("System size", "All")])
    cards = [("Fleet uptime", f"{m['Fleet Uptime %']:.1%}", GREEN if m["Fleet Uptime %"] >= 0.98 else AMBER,
              f"{m['Fleet Uptime %'] - 0.98:+.1%} vs 98% target"),
             ("Generation (kWh)", fmt_int(m["Total Generation kWh"]), INK, None),
             ("Critical alerts", fmt_int(m["Critical Alerts"]), RED, "full days of zero output"),
             ("Alerts per 1,000 site-days", f"{m['Alerts per 1,000 Site-Days']:.1f}", INK, None)]
    for i, (lab, val, col, note) in enumerate(cards):
        kpi(fig, (0.02 + i * 0.245, 0.74, 0.235, 0.15), lab, val, col, note)

    ax = panel(fig, (0.02, 0.05, 0.58, 0.67), "Daily generation and rolling 28-day total")
    g = d["daily"].groupby("date")["gen_kwh"].sum().sort_index()
    g.index = pd.to_datetime(g.index)
    ax.plot(g.index, g.values, color=AMBER, lw=0.9, label="Total Generation kWh")
    ax2 = ax.twinx()
    ax2.plot(g.index, g.rolling(28).sum().values, color=BLUE, lw=2, label="Generation Rolling 28d")
    ax.set_ylabel("kWh per day"); ax2.set_ylabel("kWh, rolling 28 days", color=BLUE)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y")); style(ax); ax2.spines[["top"]].set_visible(False)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v/1000:,.0f}k"))
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper center", frameon=False, ncol=2, fontsize=9)

    ax = panel(fig, (0.615, 0.05, 0.365, 0.67), "Alerts by month and type")
    a = d["alerts"].copy()
    a["month"] = pd.to_datetime(a["date"]).dt.to_period("M")
    piv = a.pivot_table(index="month", columns="alert_type", values="site_id", aggfunc="count").fillna(0)
    bottom = None
    for t in [c for c in ALERT_COLOURS if c in piv]:
        ax.bar(piv.index.strftime("%b\n%y"), piv[t], bottom=bottom, color=ALERT_COLOURS[t], label=t.replace("_", " "))
        bottom = piv[t] if bottom is None else bottom + piv[t]
    ax.tick_params(axis="x", labelsize=7.5); style(ax); ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    return fig


def site_drilldown(d, m):
    fig = canvas("Which sites need a technician?", "Sites ranked by critical alerts; colour = Site Status Colour measure")
    site, daily, alerts = d["site"], d["daily"], d["alerts"]
    n_days = len(d["dates"])
    crit = alerts[alerts.severity == "critical"].groupby("site_id").size()
    under = alerts[alerts.alert_type == "underperforming"].groupby("site_id").size()
    miss = alerts[alerts.alert_type == "missing_data"].groupby("site_id").size()
    outage = daily.groupby("site_id")["is_outage_day"].sum()
    perf = daily.groupby("site_id")["perf_ratio"].median()
    t = site.set_index("site_id")[["capacity_kw", "capacity_band"]].copy()
    t["Critical"] = crit.reindex(t.index).fillna(0).astype(int)
    t["Underperf."] = under.reindex(t.index).fillna(0).astype(int)
    t["Uptime"] = 1 - (outage.reindex(t.index).fillna(0) + miss.reindex(t.index).fillna(0)) / n_days
    t["Perf. ratio"] = perf.reindex(t.index)
    t = t.sort_values(["Critical", "Underperf."], ascending=False).head(16)

    fig.patches.append(FancyBboxPatch((0.02, 0.05), 0.47, 0.84, boxstyle="round,pad=0,rounding_size=0.006",
                                      transform=fig.transFigure, fc=PANEL, ec=GRID, zorder=-2))
    fig.text(0.03, 0.86, "Sites ranked by critical alerts (top 16)", fontsize=11, weight="bold", color=INK)
    cols = ["Site", "kW", "Critical", "Underperf.", "Uptime", "Perf. ratio"]
    xs = [0.035, 0.11, 0.17, 0.25, 0.34, 0.42]
    for x, c in zip(xs, cols):
        fig.text(x, 0.815, c, fontsize=9, weight="bold", color=MUTED)
    for i, (sid, r) in enumerate(t.iterrows()):
        y = 0.78 - i * 0.044
        colour = RED if r["Critical"] > 0 else AMBER if r["Underperf."] > 0 else GREEN
        fig.patches.append(FancyBboxPatch((0.03, y - 0.012), 0.45, 0.036, transform=fig.transFigure,
                                          boxstyle="round,pad=0,rounding_size=0.003", fc=colour, alpha=0.14, ec="none", zorder=-1))
        vals = [str(sid), f"{r['capacity_kw']:.2f}", str(r["Critical"]), str(r["Underperf."]),
                f"{r['Uptime']:.1%}", "–" if pd.isna(r["Perf. ratio"]) else f"{r['Perf. ratio']:.2f}"]
        for x, v in zip(xs, vals):
            fig.text(x, y, v, fontsize=9.5, color=INK)

    worst = t.index[0]
    ax = panel(fig, (0.505, 0.47, 0.475, 0.42), f"Site {worst}: performance ratio vs fleet median (1.0 = typical)")
    s = daily[daily.site_id == worst].sort_values("date")
    ax.plot(pd.to_datetime(s["date"]), s["perf_ratio"], color=BLUE, lw=0.9)
    ax.plot(pd.to_datetime(s["date"]), s["perf_ratio"].rolling(7, min_periods=3).median(), color=INK, lw=1.8, label="7-day median")
    ax.axhline(1, color=GREEN, ls="--", lw=1, label="fleet median")
    ax.set_ylim(0, max(2, s["perf_ratio"].quantile(0.99) or 2)); ax.legend(frameon=False, fontsize=8.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y")); style(ax)

    fig.patches.append(FancyBboxPatch((0.505, 0.05), 0.475, 0.4, boxstyle="round,pad=0,rounding_size=0.006",
                                      transform=fig.transFigure, fc=PANEL, ec=GRID, zorder=-2))
    fig.text(0.515, 0.42, f"Site {worst}: alert history (latest 9)", fontsize=11, weight="bold", color=INK)
    hist = alerts[alerts.site_id == worst].sort_values("date", ascending=False).head(9)
    for x, c in zip([0.515, 0.6, 0.67, 0.78], ["Date", "Severity", "Type", "Detail"]):
        fig.text(x, 0.385, c, fontsize=9, weight="bold", color=MUTED)
    for i, (_, r) in enumerate(hist.iterrows()):
        y = 0.355 - i * 0.032
        fig.text(0.515, y, f"{pd.Timestamp(r['date']):%d %b %Y}", fontsize=9, color=INK)
        fig.text(0.6, y, r["severity"], fontsize=9, color=RED if r["severity"] == "critical" else AMBER)
        fig.text(0.67, y, r["alert_type"].replace("_", " "), fontsize=9, color=INK)
        fig.text(0.78, y, (r["detail"] or "")[:38], fontsize=8.5, color=MUTED)
    return fig


def pipeline_health(d, m):
    fig = canvas("Pipeline health: can we trust today's data?", "Ingest runs, validation quarantine and completeness")
    runs = d["runs"]
    status = str(m["Last Run Status"])
    finished = m["Last Run Finished"]
    cards = [("Last run status", status, GREEN if status in ("ok", "success", "succeeded") else RED, None),
             ("Last run finished", f"{pd.Timestamp(finished):%d %b %H:%M}" if finished is not None else "–", INK, None),
             ("Quarantine rate", f"{m['Quarantine Rate %']:.3%}", INK, f"{fmt_int(m['Rows Quarantined'])} rows kept with a reason"),
             ("Data completeness", f"{m['Data Completeness %']:.1%}", INK, "half-hours received / expected")]
    for i, (lab, val, col, note) in enumerate(cards):
        kpi(fig, (0.02 + i * 0.245, 0.74, 0.235, 0.15), lab, val, col, note)

    ax = panel(fig, (0.02, 0.05, 0.47, 0.67), "Rows loaded and quarantined per run")
    lab = runs["date_from"].astype(str).str[:7]
    ax.bar(lab, runs["rows_loaded"], color=BLUE, label="rows loaded")
    ax2 = ax.twinx()
    ax2.plot(lab, runs["rows_quarantined"], color=RED, marker="o", lw=1.5, label="rows quarantined")
    ax.tick_params(axis="x", rotation=60, labelsize=8); style(ax); ax2.spines[["top"]].set_visible(False)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v/1e3:,.0f}k"))
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8.5, loc="lower left")

    fig.patches.append(FancyBboxPatch((0.505, 0.05), 0.475, 0.67, boxstyle="round,pad=0,rounding_size=0.006",
                                      transform=fig.transFigure, fc=PANEL, ec=GRID, zorder=-2))
    fig.text(0.515, 0.69, "Ingest runs (latest 14)", fontsize=11, weight="bold", color=INK)
    hdr = ["Run", "From", "To", "Rows in", "Quarantined", "Status", "Secs"]
    xs = [0.515, 0.55, 0.63, 0.71, 0.79, 0.88, 0.94]
    for x, c in zip(xs, hdr):
        fig.text(x, 0.655, c, fontsize=9, weight="bold", color=MUTED)
    for i, (_, r) in enumerate(runs.sort_values("run_id", ascending=False).head(14).iterrows()):
        y = 0.62 - i * 0.04
        vals = [r["run_id"], r["date_from"], r["date_to"], fmt_int(r["rows_in"] or 0),
                fmt_int(r["rows_quarantined"] or 0), r["status"], r["duration_seconds"] if pd.notna(r["duration_seconds"]) else "–"]
        for x, v in zip(xs, vals):
            fig.text(x, y, str(v), fontsize=8.8, color=GREEN if v == r["status"] and v == "ok" else INK)
    return fig


def metric_explorer(d, m):
    fig = canvas("Metric explorer", "Time Comparison calculation group applied to Total Generation kWh")
    g = d["daily"].copy()
    g["month"] = pd.to_datetime(g["date"]).dt.to_period("M")
    mon = g.groupby("month")["gen_kwh"].sum()
    daily_tot = g.groupby("date")["gen_kwh"].sum()
    daily_tot.index = pd.to_datetime(daily_tot.index)
    tab = pd.DataFrame({"Current": mon, "Prior Month": mon.shift(1)})
    tab["MoM Change"] = tab["Current"] - tab["Prior Month"]
    tab["MoM %"] = tab["MoM Change"] / tab["Prior Month"]
    tab["YTD"] = mon.groupby(mon.index.year).cumsum()
    tab["Rolling 28d"] = [daily_tot[:p.end_time.normalize()].tail(28).sum() for p in mon.index]

    ax = panel(fig, (0.02, 0.5, 0.96, 0.39), "Selected metric by month   [Metric Selector: Generation (kWh)]")
    ax.bar(mon.index.strftime("%b %y"), mon.values, color=AMBER)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v/1e3:,.0f}k")); style(ax)

    fig.patches.append(FancyBboxPatch((0.02, 0.05), 0.96, 0.43, boxstyle="round,pad=0,rounding_size=0.006",
                                      transform=fig.transFigure, fc=PANEL, ec=GRID, zorder=-2))
    fig.text(0.03, 0.45, "Generation (kWh) by Time Comparison item", fontsize=11, weight="bold", color=INK)
    cols = list(tab.columns)
    xs = [0.035] + [0.17 + i * 0.135 for i in range(len(cols))]
    fig.text(xs[0], 0.415, "Month", fontsize=9, weight="bold", color=MUTED)
    for x, c in zip(xs[1:], cols):
        fig.text(x, 0.415, c, fontsize=9, weight="bold", color=MUTED)
    rows = tab.tail(9)
    for i, (p, r) in enumerate(rows.iterrows()):
        y = 0.38 - i * 0.034
        fig.text(xs[0], y, p.strftime("%b %Y"), fontsize=9.5, color=INK)
        for x, c in zip(xs[1:], cols):
            v = r[c]
            txt = "–" if pd.isna(v) else (f"{v:+.1%}" if c == "MoM %" else f"{v:+,.0f}" if c == "MoM Change" else f"{v:,.0f}")
            colour = INK if c not in ("MoM %", "MoM Change") or pd.isna(v) else (GREEN if v >= 0 else RED)
            fig.text(x, y, txt, fontsize=9.5, color=colour)
    return fig


if __name__ == "__main__":
    data = load()
    if data["daily"].empty:
        sys.exit("No data in reporting.fact_daily. Run `python -m fleethealth backfill` first.")
    m = measures(data)
    OUT.mkdir(exist_ok=True)
    for name, fn in [("01_fleet_overview", fleet_overview), ("02_site_drilldown", site_drilldown),
                     ("03_pipeline_health", pipeline_health), ("04_metric_explorer", metric_explorer)]:
        fn(data, m).savefig(OUT / f"{name}.png", facecolor=BG)
        plt.close("all")
    print(f"wrote 4 previews to {OUT}")
