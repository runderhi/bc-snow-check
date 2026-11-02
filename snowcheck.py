#!/usr/bin/env python3
"""BC snow check: daily independent-vs-resort snow report for BC ski resorts.

Sources
  Independent: BC automated snow weather stations (gov.bc.ca open data, hourly)
               Avalanche Canada public forecast API
  Resort:      each resort's own snow-report page (best effort)

Writes data/latest.json, data/history/<date>.json, debug/*.txt and docs/index.html.
Set SNOW_FIXTURES=<dir> to read local sample files instead of the network.
"""
import csv
import html
import io
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HIST = DATA / "history"
DEBUG = ROOT / "debug"
DOCS = ROOT / "docs"

ASWS = "https://www.env.gov.bc.ca/wsd/data_searches/snow/asws/data/{}.csv"
STATIONS_WFS = (
    "https://openmaps.gov.bc.ca/geo/pub/wfs?service=WFS&version=2.0.0&request=GetFeature"
    "&typeName=pub:WHSE_WATER_MANAGEMENT.SSL_SNOW_ASWS_STNS_SP"
    "&outputFormat=application%2Fjson&srsName=EPSG:4326"
)
STATIONS_ARCGIS = (
    "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/bcgw_pub_whse_water_management/"
    "MapServer/31/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=json"
)
AVCAN = "https://avcan-services-api.prod.avalanche.ca/forecasts/en/{}"
STATION_MAP = "https://governmentofbc.maps.arcgis.com/apps/webappviewer/index.html?id=c15768bf73494f5da04b1aac6793bd2e"

FIXTURES = os.environ.get("SNOW_FIXTURES")
UA = {"User-Agent": "bc-snow-check/1.0 (personal daily snow report; github actions)",
      "Accept-Language": "en-CA,en;q=0.9"}
health = []  # (source, ok, note)


def log_health(source, ok, note=""):
    health.append({"source": source, "ok": ok, "note": note})
    print(("OK   " if ok else "FAIL ") + source + (f" - {note}" if note else ""), file=sys.stderr)


def fetch(key, url, as_json=False):
    if FIXTURES:
        p = Path(FIXTURES) / key
        if not p.exists():
            raise FileNotFoundError(f"fixture {key} missing")
        txt = p.read_text(encoding="utf-8")
        return json.loads(txt) if as_json else txt
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=45)
            r.raise_for_status()
            return r.json() if as_json else r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def num(v):
    try:
        f = float(str(v).strip())
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- stations
def load_station_locations():
    cache = DATA / "stations.json"
    if cache.exists() and not FIXTURES:
        age = time.time() - cache.stat().st_mtime
        if age < 7 * 86400:
            return json.loads(cache.read_text())
    stations = None
    for key, url in (("stations.json", STATIONS_WFS), ("stations_arcgis.json", STATIONS_ARCGIS)):
        try:
            raw = fetch(key, url, as_json=True)
            stations = parse_station_locations(raw)
            if stations:
                break
        except Exception as e:  # noqa: BLE001
            print(f"station locations via {key} failed: {e}", file=sys.stderr)
    if stations:
        if not FIXTURES:
            cache.write_text(json.dumps(stations, indent=1))
        log_health("BC station locations", True, f"{len(stations)} stations")
        return stations
    if cache.exists():
        log_health("BC station locations", False, "refresh failed, using cached list")
        return json.loads(cache.read_text())
    log_health("BC station locations", False, "unavailable; only pinned stations will work")
    return {}


def parse_station_locations(raw):
    out = {}
    feats = raw.get("features", []) if isinstance(raw, dict) else []
    for f in feats:
        props = f.get("properties") or f.get("attributes") or {}
        p = {k.upper(): v for k, v in props.items()}
        sid = str(p.get("LOCATION_ID") or "").strip()
        if not sid:
            continue
        lat, lon = num(p.get("LATITUDE")), num(p.get("LONGITUDE"))
        g = f.get("geometry") or {}
        if (lat is None or lon is None) and g:
            if "coordinates" in g:
                lon, lat = g["coordinates"][:2]
            elif "x" in g:
                lon, lat = g["x"], g["y"]
        if lat is None or lon is None:
            continue
        status = str(p.get("STATUS") or "").lower()
        out[sid] = {
            "id": sid,
            "name": str(p.get("LOCATION_NAME") or sid).title(),
            "lat": float(lat), "lon": float(lon),
            "elev": num(p.get("ELEVATION")),
            "active": "inactive" not in status,
        }
    return out


DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d")


def parse_dt(s):
    s = s.strip().replace("Z", "")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


STN_RE = re.compile(r"^\s*(\d[A-Z]\d{2}[A-Z])\b")


def load_asws(var):
    """Return {station_id: [(datetime, value), ...]} for one ASWS variable CSV."""
    text = fetch(f"{var}.csv", ASWS.format(var))
    rows = list(csv.reader(io.StringIO(text)))
    header_i = next(i for i, r in enumerate(rows[:10]) if sum(bool(STN_RE.match(c)) for c in r) >= 3)
    header = rows[header_i]
    cols = {i: STN_RE.match(c).group(1) for i, c in enumerate(header) if STN_RE.match(c)}
    series = {sid: [] for sid in cols.values()}
    for r in rows[header_i + 1:]:
        if not r:
            continue
        dt = parse_dt(r[0])
        if not dt:
            continue
        for i, sid in cols.items():
            if i < len(r):
                v = num(r[i])
                if v is not None:
                    series[sid].append((dt, v))
    for s in series.values():
        s.sort(key=lambda x: x[0])
    return series


def value_near(series, when, tol_hours=6):
    best = None
    for dt, v in series:
        d = abs((dt - when).total_seconds()) / 3600
        if d <= tol_hours and (best is None or d < best[0]):
            best = (d, v)
    return best[1] if best else None


def station_summary(sid, meta, sd, sw, ta):
    s = sd.get(sid, [])
    out = {"id": sid, "name": meta.get("name", sid), "elev": meta.get("elev"),
           "dist_km": meta.get("dist_km"), "depth": None}
    if not s:
        return out
    t, depth = s[-1]
    out.update({
        "time": t.isoformat(),
        "age_h": round((datetime.now(timezone.utc) - t).total_seconds() / 3600, 1),
        "depth": round(max(depth, 0)),
        "d24": delta(depth, value_near(s, t - timedelta(hours=24))),
        "d7": delta(depth, value_near(s, t - timedelta(days=7), tol_hours=12)),
        "swe": rnd(sw.get(sid, [])[-1][1]) if sw.get(sid) else None,
        "temp": round(ta[sid][-1][1], 1) if ta.get(sid) else None,
        "daily": daily_series(s),
    })
    return out


def delta(now, then):
    return None if then is None else round(now - then)


def rnd(v):
    return None if v is None else round(max(v, 0))


def daily_series(s):
    """Last reading of each UTC day, from the start of the water year."""
    by_day = {}
    for dt, v in s:
        by_day[dt.date().isoformat()] = round(max(v, 0))
    return by_day


def pick_stations(resort, locs, sd, cfg):
    picked = []
    for sid in resort.get("pinned_stations", []):
        meta = dict(locs.get(sid, {"name": sid}))
        if "lat" in meta:
            meta["dist_km"] = round(km(resort["lat"], resort["lon"], meta["lat"], meta["lon"]))
        picked.append((sid, meta))
    cands = []
    for sid, m in locs.items():
        if sid in dict(picked) or not m.get("active") or not sd.get(sid):
            continue
        d = km(resort["lat"], resort["lon"], m["lat"], m["lon"])
        if d <= cfg["station_search_radius_km"]:
            cands.append((d, sid, m))
    cands.sort()
    for d, sid, m in cands:
        if len(picked) >= cfg["stations_per_resort"]:
            break
        picked.append((sid, {**m, "dist_km": round(d)}))
    return picked


# ---------------------------------------------------------------- avalanche canada
def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][:2]
        xj, yj = ring[j][:2]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geom(lon, lat, g):
    if not g:
        return False
    polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) \
        if g.get("type") == "MultiPolygon" else []
    for poly in polys:
        if poly and point_in_ring(lon, lat, poly[0]) and not any(point_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def strip_html(s, limit=420):
    t = re.sub(r"\s+", " ", BeautifulSoup(s or "", "html.parser").get_text(" ")).strip()
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


def rating_text(r):
    if not isinstance(r, dict):
        return None
    inner = r.get("rating") if isinstance(r.get("rating"), dict) else r
    return inner.get("display") or inner.get("value")


def load_avcan():
    try:
        areas = fetch("avcan_areas.json", AVCAN.format("areas"), as_json=True)
        products = fetch("avcan_products.json", AVCAN.format("products"), as_json=True)
        if isinstance(products, dict):
            products = products.get("products") or products.get("items") or []
        log_health("Avalanche Canada forecasts", True, f"{len(products)} products")
        return areas.get("features", []), products
    except Exception as e:  # noqa: BLE001
        log_health("Avalanche Canada forecasts", False, str(e)[:120])
        return [], []


def avcan_for(resort, areas, products):
    area_id = next((f.get("id") or (f.get("properties") or {}).get("id") for f in areas
                    if point_in_geom(resort["lon"], resort["lat"], f.get("geometry"))), None)
    nearest_note = None
    if not area_id:
        best = None
        for f in areas:
            g = f.get("geometry") or {}
            polys = [g.get("coordinates")] if g.get("type") == "Polygon" else g.get("coordinates") or []
            for poly in polys:
                for lon, lat in (pt[:2] for pt in (poly[0] if poly else [])):
                    d = km(resort["lat"], resort["lon"], lat, lon)
                    if best is None or d < best[0]:
                        best = (d, f.get("id") or (f.get("properties") or {}).get("id"))
        if not best or best[0] > 75:
            return {"status": "Outside Avalanche Canada forecast regions"}
        area_id, nearest_note = best[1], f"Nearest region, about {round(best[0])} km away"
    prod = next((p for p in products if (p.get("area") or {}).get("id") == area_id), None)
    if not prod:
        return {"status": "Region found but no current forecast product"}
    rep = prod.get("report") or {}
    dr = (rep.get("dangerRatings") or [{}])[0]
    ratings = dr.get("ratings") or {}
    sums = {}
    for s in rep.get("summaries") or []:
        typ = (s.get("type") or {}).get("value") or ""
        sums[typ] = strip_html(s.get("content"))
    danger = {band: rating_text(ratings.get(band)) for band in ("alp", "tln", "btl")}
    no_rating = all((d or "").lower().replace(" ", "") in ("norating", "") for d in danger.values())
    return {
        "status": "ok",
        "region": rep.get("title") or (prod.get("area") or {}).get("name"),
        "region_note": nearest_note,
        "url": prod.get("url"),
        "danger": danger,
        "no_rating": no_rating,
        "highlights": strip_html(rep.get("highlights"), 300),
        "snowpack": sums.get("snowpack-summary"),
        "weather": sums.get("weather-summary"),
        "avalanche": sums.get("avalanche-summary"),
    }


# ---------------------------------------------------------------- resort pages
FIELD_LABELS = {
    "new24": [r"24\s*-?\s*h(?:(?:ou)?rs?)?\b", r"overnight", r"new snow(?: \(since[^)]*\))?", r"last 24"],
    "new48": [r"48\s*-?\s*h(?:(?:ou)?rs?)?\b"],
    "new7d": [r"7\s*-?\s*days?", r"seven days?", r"past week", r"this week"],
    "base": [r"(?:alpine |mid[- ]mountain |upper |summit )?base(?: depth)?", r"snow base", r"settled base"],
    "season": [r"season(?:al)? total", r"season to date", r"cumulative", r"total snowfall", r"season snowfall"],
}
LIMITS = {"new24": 150, "new48": 250, "new7d": 500, "base": 600, "season": 2000}
NUM_UNIT = r"(\d{1,4}(?:\.\d)?)\s*(cm|centimet(?:er|re)s?|\"|”|in(?:ches)?\b)"


def to_cm(v, unit):
    v = float(v)
    return round(v * 2.54) if unit and unit.lower()[0] in ('"', "”", "i") else round(v)


def from_vail(page):
    m = re.search(r"FR\.snowReportData\s*=\s*(\{.*?\})\s*;", page, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return {}

    def cm(key):
        v = d.get(key)
        return num(v.get("Centimeters")) if isinstance(v, dict) else None
    out = {"new24": cm("TwentyFourHourSnowfall"), "new48": cm("FortyEightHourSnowfall"),
           "new7d": cm("SevenDaySnowfall"), "base": cm("BaseDepth"), "season": cm("CurrentSeason")}
    return {k: round(v) for k, v in out.items() if v is not None}


def from_text(text):
    out = {}
    low = text.lower()
    for field, labels in FIELD_LABELS.items():
        for lab in labels:
            for m in re.finditer(lab, low):
                window = low[m.end(): m.end() + 70]
                n = re.search(NUM_UNIT, window)
                if n and n.start() < 45:
                    v = to_cm(n.group(1), n.group(2))
                    if 0 <= v <= LIMITS[field]:
                        out[field] = v
                        break
                before = low[max(0, m.start() - 25): m.start()]
                n = re.search(NUM_UNIT + r"\s*[|:]?\s*$", before)
                if n:
                    v = to_cm(n.group(1), n.group(2))
                    if 0 <= v <= LIMITS[field]:
                        out[field] = v
                        break
            if field in out:
                break
    lm = re.search(r"(\d{1,2})\s*(?:/|of)\s*(\d{1,2})\s*lifts?", low) or \
        re.search(r"lifts?(?: open)?\s*[|:]?\s*(\d{1,2})\s*(?:/|of)\s*(\d{1,2})", low)
    if lm and int(lm.group(1)) <= int(lm.group(2)):
        out["lifts"] = f"{lm.group(1)} / {lm.group(2)}"
    return out


def table_pairs(soup):
    """Turn header-row/value-row tables and dt/dd lists into 'label: value' lines."""
    lines = []
    for table in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        for a, b in zip(rows, rows[1:]):
            if len(a) == len(b) > 1 and not any(re.search(NUM_UNIT, c.lower()) for c in a) \
                    and any(re.search(NUM_UNIT, c.lower()) for c in b):
                lines += [f"{h}: {v}" for h, v in zip(a, b)]
        lines += [" : ".join(r) for r in rows if len(r) == 2]
    for dl in soup.find_all("dl"):
        lines += [f"{dt.get_text(' ', strip=True)}: {dd.get_text(' ', strip=True)}"
                  for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd"))]
    return " | ".join(lines)


def resort_report(resort):
    try:
        page = fetch(f"resort_{resort['id']}.html", resort["report_url"])
    except Exception as e:  # noqa: BLE001
        log_health(f"{resort['name']} resort page", False, str(e)[:120])
        return {"status": "Page could not be loaded"}
    soup = BeautifulSoup(page, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    text = re.sub(r"[ \t]+", " ", soup.get_text(" | "))
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / f"{resort['id']}.txt").write_text(text[:60000], encoding="utf-8")
    vals = from_vail(page)
    for k, v in from_text(table_pairs(soup)).items():
        vals.setdefault(k, v)
    for k, v in from_text(text).items():
        vals.setdefault(k, v)
    got = [k for k in ("new24", "new7d", "base", "season") if k in vals]
    if got:
        log_health(f"{resort['name']} resort page", True, "found " + ", ".join(got))
        vals["status"] = "ok"
    else:
        log_health(f"{resort['name']} resort page", False,
                   "loaded but no snow numbers found (not posted yet, or page needs a parser update)")
        vals["status"] = "No snow numbers published"
    return vals


# ---------------------------------------------------------------- comparison
def agreement(resort_vals, stn):
    if not stn or stn.get("depth") is None:
        return None, "No station data"
    for rk, sk, span in (("new7d", "d7", "7-day"), ("new24", "d24", "24-hour")):
        r, s = resort_vals.get(rk), stn.get(sk)
        if r is None or s is None:
            continue
        s = max(s, 0)
        if r < 3 and s < 3:
            return "close", f"{span}: both near zero"
        if r >= 2 * max(s, 1) + 8:
            return "resort_higher", f"{span}: resort {r} cm new vs station +{s} cm depth"
        if s >= r + 15:
            return "station_higher", f"{span}: station +{s} cm depth vs resort {r} cm new"
        return "close", f"{span}: resort {r} cm new vs station +{s} cm depth"
    return None, "Resort hasn't published new-snow numbers"


# ---------------------------------------------------------------- main
def main():
    cfg = json.loads((ROOT / "config.json").read_text())
    for d in (DATA, HIST, DEBUG, DOCS):
        d.mkdir(parents=True, exist_ok=True)
    locs = load_station_locations()
    sd, sw, ta = {}, {}, {}
    for var, target in (("SD", sd), ("SW", sw), ("TA", ta)):
        try:
            target.update(load_asws(var))
            log_health(f"BC snow stations ({var})", True, f"{len(target)} stations")
        except Exception as e:  # noqa: BLE001
            log_health(f"BC snow stations ({var})", False, str(e)[:120])
    areas, products = load_avcan()

    now = datetime.now(timezone.utc)
    resorts = []
    for r in cfg["resorts"]:
        stns = [station_summary(sid, meta, sd, sw, ta) for sid, meta in pick_stations(r, locs, sd, cfg)]
        primary = next((s for s in stns if s.get("depth") is not None), None)
        rep = resort_report(r)
        flag, note = agreement(rep, primary)
        resorts.append({
            "id": r["id"], "name": r["name"], "summit_m": r.get("summit_m"),
            "report_url": r["report_url"], "local_links": r.get("local_links", []),
            "stations": stns, "avalanche": avcan_for(r, areas, products) if areas else {"status": "unavailable"},
            "resort": rep, "agreement": flag, "agreement_note": note,
        })
    snap = {"generated": now.isoformat(), "resorts": resorts, "health": health}
    (DATA / "latest.json").write_text(json.dumps(snap, indent=1))
    lean = {"generated": snap["generated"], "resorts": [
        {"id": x["id"], "resort": {k: v for k, v in x["resort"].items() if k != "status"},
         "stations": [{k: s.get(k) for k in ("id", "depth", "d24", "d7", "swe")} for s in x["stations"]]}
        for x in resorts]}
    (HIST / f"{now.date().isoformat()}.json").write_text(json.dumps(lean, indent=1))
    from render import render
    (DOCS / "index.html").write_text(render(snap, load_history()), encoding="utf-8")
    print(f"Wrote report for {len(resorts)} resorts; {sum(h['ok'] for h in health)}/{len(health)} sources ok",
          file=sys.stderr)


def load_history():
    out = {}
    for p in sorted(HIST.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        for r in d.get("resorts", []):
            b = (r.get("resort") or {}).get("base")
            if b is not None:
                out.setdefault(r["id"], {})[p.stem] = b
    return out


if __name__ == "__main__":
    main()
