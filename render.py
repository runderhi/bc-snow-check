"""Render the daily snow report as one self-contained, phone-friendly HTML page."""
from datetime import datetime, timezone
from html import escape

try:
    from zoneinfo import ZoneInfo
    PT = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001
    PT = timezone.utc

STATION_MAP = "https://governmentofbc.maps.arcgis.com/apps/webappviewer/index.html?id=c15768bf73494f5da04b1aac6793bd2e"
DANGER_CLASS = {"low": "d1", "moderate": "d2", "considerable": "d3", "high": "d4", "extreme": "d5"}
AGREE = {
    "close": ("Close", "ok"),
    "resort_higher": ("Resort higher", "warn"),
    "station_higher": ("Station higher", "info"),
}


def e(v):
    return escape("" if v is None else str(v))


def cm(v, signed=False):
    if v is None:
        return '<span class="na">—</span>'
    return f"{'+' if signed and v > 0 else ''}{v} cm"


def when(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(PT).strftime("%a %b %-d, %-I:%M %p")
    except Exception:  # noqa: BLE001
        return iso or ""


def danger_badge(av):
    if av.get("status") != "ok":
        return f'<span class="badge muted">{e(av.get("status", "No forecast"))}</span>'
    if av.get("no_rating"):
        return '<span class="badge muted">Avalanche: no rating yet</span>'
    d = av["danger"]
    top = next((v for v in (d.get("alp"), d.get("tln"), d.get("btl")) if v), "")
    cls = DANGER_CLASS.get(top.split()[-1].lower() if top else "", "muted")
    return f'<span class="badge {cls}">Alpine {e(d.get("alp"))} · Treeline {e(d.get("tln"))}</span>'


def chart(resort, base_hist):
    stn = next((s for s in resort["stations"] if s.get("daily")), None)
    series = (stn or {}).get("daily") or {}
    days = sorted(set(series) | set(base_hist))
    if len(days) < 2:
        return '<p class="note">The season chart fills in as the stations record snow.</p>'
    vals = [v for v in list(series.values()) + list(base_hist.values()) if v is not None]
    top = max(max(vals), 50)
    w, h, pad = 420, 170, 32
    x = {d: pad + i * (w - 2 * pad) / (len(days) - 1) for i, d in enumerate(days)}

    def y(v):
        return h - pad - v / top * (h - 2 * pad)
    line = " ".join(f"{x[d]:.1f},{y(series[d]):.1f}" for d in days if d in series)
    dots = "".join(f'<circle cx="{x[d]:.1f}" cy="{y(v):.1f}" r="3" class="rb"><title>{d}: resort base {v} cm</title></circle>'
                   for d, v in base_hist.items() if d in x)
    ticks = "".join(f'<text x="{x[d]:.1f}" y="{h - 8}" class="ax" text-anchor="middle">{datetime.fromisoformat(d).strftime("%b %-d")}</text>'
                    for d in days[:: max(1, len(days) // 5)])
    grid = "".join(f'<line x1="{pad}" x2="{w - pad}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                   f'<text x="{pad - 4}" y="{y(t) + 4:.1f}" class="ax" text-anchor="end">{t}</text>'
                   for t in range(0, int(top) + 1, max(25, int(top // 4 // 25 * 25) or 25)))
    label = e(stn["name"]) if stn else "Station"
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Season snow depth">'
            f'{grid}<polyline points="{line}" class="sl"/>{dots}{ticks}</svg>'
            f'<p class="legend"><span class="key sl-key"></span>{label} depth (cm)'
            f'<span class="key rb-key"></span>Resort-reported base</p>')


def station_rows(stations):
    if not stations:
        return '<p class="note">No active snow station within range.</p>'
    out = []
    for s in stations:
        if s.get("depth") is None and s.get("swe") is None:
            out.append(f'<div class="stn"><div class="row"><span>{e(s["name"])} ({e(s["id"])})</span><span class="na">No current data</span></div></div>')
            continue
        where = " · ".join(p for p in (f'{s["elev"]:.0f} m' if s.get("elev") else "",
                                        f'{s["dist_km"]} km away' if s.get("dist_km") is not None else "") if p)
        stale = ' <span class="warn-t">(stale)</span>' if (s.get("age_h") or 0) > 30 else ""
        top = cm(s["depth"]) if s.get("depth") is not None else f'{s["swe"]} mm SWE'
        depth_row = (f'<div class="row"><span class="sub">Depth change 24h / 7d</span><span>{cm(s.get("d24"), True)} / {cm(s.get("d7"), True)}</span></div>'
                     if s.get("depth") is not None else
                     '<div class="row"><span class="sub">Snow depth</span><span class="na">sensor not reporting</span></div>')
        swe_chg = (f' <span class="sub">({s["swe_d24"]:+d} / {s["swe_d7"]:+d} mm)</span>'
                   if s.get("swe_d24") is not None and s.get("swe_d7") is not None else "")
        out.append(
            f'<div class="stn"><div class="row"><span><b>{e(s["name"])}</b> <span class="sub">{e(s["id"])} · {where}</span></span>'
            f'<span class="big">{top}</span></div>'
            f'{depth_row}'
            f'<div class="row"><span class="sub">Snow water equivalent (24h / 7d)</span><span>{s["swe"] if s.get("swe") is not None else "—"} mm{swe_chg}</span></div>'
            f'<div class="row"><span class="sub">Air temp · reading time</span><span>{s["temp"] if s.get("temp") is not None else "—"}°C · {when(s.get("time"))}{stale}</span></div></div>')
    return "".join(out)


def resort_rows(rep):
    if rep.get("status") != "ok":
        return f'<p class="note">{e(rep.get("status"))}</p>'
    items = [("New 24h", cm(rep.get("new24"))), ("New 48h", cm(rep.get("new48"))), ("New 7 days", cm(rep.get("new7d"))),
             ("Base depth", cm(rep.get("base"))), ("Season total", cm(rep.get("season"))), ("Lifts open", e(rep.get("lifts")) or "—")]
    rows = "".join(f'<div class="row"><span class="sub">{k}</span><span>{v}</span></div>' for k, v in items)
    if rep.get("updated"):
        rows += f'<p class="note">Resort last updated {e(rep["updated"])}</p>'
    return rows


def card(r, base_hist):
    av = r["avalanche"]
    notes = ""
    if av.get("status") == "ok":
        bits = [(lbl, av.get(k)) for lbl, k in (("Highlights", "highlights"), ("Snowpack", "snowpack"), ("Weather", "weather")) if av.get(k)]
        notes = "".join(f'<p class="fc"><span class="sub">{lbl}:</span> {e(t)}</p>' for lbl, t in bits[:2])
        notes += f'<p class="src"><a href="{e(av.get("url"))}">{e(av.get("region"))} forecast</a>{" · " + e(av["region_note"]) if av.get("region_note") else ""}</p>' if av.get("url") else ""
    links = "".join(f' · <a href="{e(l["url"])}">{e(l["name"])}</a>' for l in r.get("local_links", []))
    return f'''
<section class="card" id="{e(r["id"])}">
  <div class="head"><h2>{e(r["name"])}</h2>{danger_badge(av)}</div>
  <div class="cols">
    <div class="pane ind"><h3>Independent measurements</h3>{station_rows(r["stations"])}{notes}</div>
    <div class="pane"><h3>Resort reported</h3>{resort_rows(r["resort"])}
      <p class="src"><a href="{e(r["report_url"])}">Resort snow report</a>{links}</p></div>
  </div>
  <p class="agree">{e(r.get("agreement_note"))}</p>
  {chart(r, base_hist)}
</section>'''


def summary(resorts):
    rows = []
    for r in resorts:
        s = next((x for x in r["stations"] if x.get("depth") is not None), {})
        lbl, cls = AGREE.get(r.get("agreement"), ("—", "na"))
        rep = r["resort"]
        rows.append(f'<tr><td><a href="#{e(r["id"])}">{e(r["name"])}</a></td>'
                    f'<td>{cm(s.get("depth"))} <span class="chg">{cm(s.get("d24"), True) if s.get("d24") is not None else ""}</span></td>'
                    f'<td>{cm(rep.get("base"))}</td><td>{cm(rep.get("new24"))}</td><td class="{cls}">{lbl}</td></tr>')
    return ('<table class="sum"><thead><tr><th>Resort</th><th>Station depth (24h)</th><th>Resort base</th>'
            '<th>Resort new 24h</th><th>Agreement</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def render(snap, history):
    ok = sum(h["ok"] for h in snap["health"])
    fails = [h for h in snap["health"] if not h["ok"]]
    health_list = "".join(f'<li><span class="{"ok" if h["ok"] else "warn"}">{"✓" if h["ok"] else "✗"}</span> '
                          f'{e(h["source"])} <span class="sub">{e(h["note"])}</span></li>' for h in snap["health"])
    cards = "".join(card(r, history.get(r["id"], {})) for r in snap["resorts"])
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>BC snow check</title>
<style>
:root{{--bg:#f6f5f1;--card:#fff;--text:#1d1d1b;--sub:#6b6a66;--line:#e2e0d9;--accent:#1f6fb2;--ok:#2f7d32;--warn:#b5650b;--info:#1f6fb2;
--d1:#dff0d8;--d2:#fff3c4;--d3:#ffd9b3;--d4:#ffc2c2;--d5:#e6c8f0;--ind:#2f7d32}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1d;--text:#ecebe6;--sub:#a3a29c;--line:#34332f;--accent:#6fb2ec;
--ok:#7cc47f;--warn:#f0a858;--info:#6fb2ec;--d1:#24402a;--d2:#4a4220;--d3:#5a3a1c;--d4:#5c2525;--d5:#45284f;--ind:#7cc47f}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:16px}}h1{{font-size:22px;margin:4px 0 0;font-weight:600}}h2{{font-size:18px;margin:0;font-weight:600}}
h3{{font-size:12px;margin:0 0 8px;font-weight:600;color:var(--sub);text-transform:uppercase;letter-spacing:.04em}}
a{{color:var(--accent);text-decoration:none}}.sub,.note,.src,.agree{{color:var(--sub);font-size:13px}}.na{{color:var(--sub)}}
.updated{{color:var(--sub);font-size:13px;margin:0 0 16px}}
.sum{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;font-size:14px;margin-bottom:16px}}
.sum th{{text-align:left;font-weight:500;color:var(--sub);font-size:12px;padding:8px 10px;border-bottom:1px solid var(--line)}}
.sum td{{padding:9px 10px;border-top:1px solid var(--line)}}.chg{{color:var(--ok);font-size:12px;white-space:nowrap}}.sum td:nth-child(n+2){{white-space:nowrap}}
.ok{{color:var(--ok)}}.warn,.warn-t{{color:var(--warn)}}.info{{color:var(--info)}}
.card{{background:var(--card);border-radius:12px;padding:14px 16px;margin-bottom:14px;border:1px solid var(--line)}}
.head{{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
.badge{{font-size:12px;padding:3px 10px;border-radius:999px;color:var(--text)}}.muted{{background:var(--line)}}
.d1{{background:var(--d1)}}.d2{{background:var(--d2)}}.d3{{background:var(--d3)}}.d4{{background:var(--d4)}}.d5{{background:var(--d5)}}
.cols{{display:grid;grid-template-columns:1fr;gap:12px}}@media(min-width:620px){{.cols{{grid-template-columns:1fr 1fr}}}}
.pane{{border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.pane.ind{{border-color:var(--ind)}}.pane.ind h3{{color:var(--ind)}}
.row{{display:flex;justify-content:space-between;gap:10px;padding:2px 0}}.big{{font-size:17px;font-weight:600;white-space:nowrap}}
.stn+.stn{{margin-top:8px;padding-top:8px;border-top:1px solid var(--line)}}.fc{{font-size:13px;margin:8px 0 0}}
.chart{{width:100%;height:auto;margin-top:8px}}.sl{{fill:none;stroke:var(--ind);stroke-width:2}}.rb{{fill:var(--warn)}}
.grid{{stroke:var(--line)}}.ax{{fill:var(--sub);font-size:13px}}
.legend{{font-size:12px;color:var(--sub);margin:2px 0 0}}.key{{display:inline-block;width:12px;height:3px;margin:0 5px 3px 10px;vertical-align:middle}}
.sl-key{{background:var(--ind)}}.rb-key{{background:var(--warn);height:7px;width:7px;border-radius:50%}}
details{{background:var(--card);border-radius:12px;padding:10px 16px;border:1px solid var(--line);margin-bottom:12px}}
summary{{cursor:pointer;font-weight:500}}ul{{padding-left:18px}}li{{margin:3px 0}}
@media(max-width:480px){{.sum th:nth-child(4),.sum td:nth-child(4){{display:none}}}}
</style></head><body><main>
<h1>BC snow check</h1>
<p class="updated">Updated {when(snap["generated"])} PT · {ok} of {len(snap["health"])} sources responded{" · " + str(len(fails)) + " need attention" if fails else ""}</p>
{summary(snap["resorts"])}
{cards}
<details><summary>Source health</summary><ul>{health_list}</ul></details>
<details><summary>How to read this</summary>
<p class="note">Independent numbers come from BC government automated snow-pillow stations (hourly, satellite-transmitted) and Avalanche Canada forecasts. Stations are usually off the ski runs, sometimes tens of kilometres away and at different elevations, so use them for trend and to sanity-check the resort. Depth change is not the same as snowfall: new snow settles, so a station gaining 8 cm can match 12–15 cm of reported new snow. "Resort higher" only appears when the gap is larger than settling explains. Resort numbers are read from each resort's own page and shown separately, never blended in.</p>
<p class="note"><a href="https://www.avalanche.ca/map">Avalanche Canada map and field reports</a> · <a href="{STATION_MAP}">BC snow station map</a> · <a href="https://www.drivebc.ca/">DriveBC cams</a></p>
</details>
</main></body></html>'''
