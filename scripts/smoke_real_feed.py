"""Run the integration's GTFS core against the real Tüke Busz feed.

    python scripts/smoke_real_feed.py [origin] [destination]
"""
import importlib.util
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tukebusz_gtfs", ROOT / "custom_components/tukebusz/gtfs.py")
gtfs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gtfs
spec.loader.exec_module(gtfs)

sys.stdout.reconfigure(encoding="utf-8")
feed = ROOT / "data" / "gtfs.zip"
if not feed.exists() or time.time() - feed.stat().st_mtime > 86400:
    feed.parent.mkdir(exist_ok=True)
    with urlopen(gtfs.GTFS_URL, timeout=120) as r:
        feed.write_bytes(r.read())

t0 = time.time()
store = gtfs.GtfsStore.load(feed)
print(f"Loaded in {time.time() - t0:.1f}s: {len(store.trips)} trips, {len(store.stops)} stops, "
      f"feed {store.feed_range()[0]} – {store.feed_range()[1]}")

origin = sys.argv[1] if len(sys.argv) > 1 else "Főpályaudvar"
dest = sys.argv[2] if len(sys.argv) > 2 else "Kertváros"
now = datetime.now(store.tz)
print(f"\nMost: {now:%Y-%m-%d %H:%M:%S}\nKövetkező 5 járat {origin} → {dest}:")
for j in store.next_journeys(origin, dest, now, 5):
    s = gtfs.serialize_journey(j, now)
    print(f"  {s['line']:>4}  {s['departure_str']} ({s['origin_platform']}) → {s['arrival_str'][11:]}"
          f"  {s['duration_min']} perc  [{s['relative']}]  {s['headsign']}")

stop = store.resolve_stop(origin)[0]
deps = store.stop_timetable(stop, now.date())
print(f"\nEgész napos menetrend: {store.stop_label([stop])} – {len(deps)} indulás")
print("  " + "  ".join(f"{d['time']}/{d['line']}" for d in deps[:20]) + (" …" if len(deps) > 20 else ""))

res = store.line_timetable("15", now.date(), dtime(16), dtime(18))
print("\n15-ös járat 16:00–18:00:")
for d in res["directions"]:
    print(f"  → {d['terminus']}: {len(d['stops'])} megálló, {len(d['trips'])} járat")
    for i, s in enumerate(d["stops"][:4]):
        print(f"     {s['name'][:25]:<25} " + " ".join(t["times"][i] or "  -  " for t in d["trips"]))
