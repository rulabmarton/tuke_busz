"""Tüke Busz (Pécs) realtime data - proof of concept.

Two layers:
  * Static GTFS from Biokom Mobilitási Központ (official, public, works).
  * Realtime adapter. The only documented realtime protocol is the legacy
    "menőBusz" handler.php API (reverse-engineered by the open-source
    BetterTuke app). It has been offline since the switch to the
    TükeBuszON+ app (HC Linear). Plug the new endpoint in via a new
    RealtimeSource subclass once it is known.

Usage:
  python tukebusz_poc.py stops <name>          search stops
  python tukebusz_poc.py departures <stop_id>  scheduled (+ realtime if available)
  python tukebusz_poc.py probe                 check realtime endpoint health
  python tukebusz_poc.py legacy-stop <id>      raw legacy SMART query
  python tukebusz_poc.py legacy-trip <id>      raw legacy BLATE query (bus position)
"""
from __future__ import annotations

import csv
import io
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8")

GTFS_URL = "https://mobilitas.biokom.hu/gtfs/"
DATA_DIR = Path(__file__).parent / "data"
GTFS_PATH = DATA_DIR / "gtfs.zip"
GTFS_MAX_AGE_S = 24 * 3600


# --------------------------------------------------------------------------- static GTFS

def ensure_gtfs() -> Path:
    if GTFS_PATH.exists() and time.time() - GTFS_PATH.stat().st_mtime < GTFS_MAX_AGE_S:
        return GTFS_PATH
    DATA_DIR.mkdir(exist_ok=True)
    print(f"Downloading GTFS from {GTFS_URL} ...", file=sys.stderr)
    with urlopen(GTFS_URL, timeout=60) as r:
        GTFS_PATH.write_bytes(r.read())
    return GTFS_PATH


class Gtfs:
    def __init__(self, path: Path):
        self.zip = zipfile.ZipFile(path)
        self.stops = {s["stop_id"]: s for s in self._read("stops.txt")}
        self.routes = {r["route_id"]: r for r in self._read("routes.txt")}
        self.trips = {t["trip_id"]: t for t in self._read("trips.txt")}

    def _read(self, name: str):
        text = self.zip.read(name).decode("utf-8-sig")
        return csv.DictReader(io.StringIO(text))

    def active_services(self, day: date) -> set[str]:
        ymd = day.strftime("%Y%m%d")
        weekday = day.strftime("%A").lower()
        active = {
            c["service_id"]
            for c in self._read("calendar.txt")
            if c["start_date"] <= ymd <= c["end_date"] and c[weekday] == "1"
        }
        for e in self._read("calendar_dates.txt"):
            if e["date"] == ymd:
                (active.add if e["exception_type"] == "1" else active.discard)(e["service_id"])
        return active

    def search_stops(self, query: str):
        q = query.lower()
        return [s for s in self.stops.values() if q in s["stop_name"].lower()]

    def departures(self, stop_id: str, now: datetime, window_min: int = 60):
        services = self.active_services(now.date())
        midnight = datetime.combine(now.date(), datetime.min.time())
        out = []
        for st in self._read("stop_times.txt"):
            if st["stop_id"] != stop_id:
                continue
            trip = self.trips.get(st["trip_id"])
            if not trip or trip["service_id"] not in services:
                continue
            h, m, s = map(int, st["departure_time"].split(":"))  # may exceed 24h
            dep = midnight + timedelta(hours=h, minutes=m, seconds=s)
            if now - timedelta(minutes=2) <= dep <= now + timedelta(minutes=window_min):
                route = self.routes[trip["route_id"]]
                out.append((dep, route["route_short_name"], trip.get("trip_headsign") or route["route_long_name"], trip["trip_id"]))
        return sorted(out)


# --------------------------------------------------------------------------- realtime

@dataclass
class Arrival:
    line: str
    name: str
    arrive: str
    trip_id: int
    remaining_min: int
    at_stop: bool


@dataclass
class VehiclePosition:
    plate: str
    stop_seq: int
    stop_id: int
    at_stop: bool
    lat: float
    lon: float
    delay_min: int
    delay_sec: int
    last_update: str


class RealtimeSource:
    """Interface for a realtime backend."""

    def health(self) -> tuple[bool, str]:
        raise NotImplementedError

    def arrivals(self, stop_id: str) -> list[Arrival]:
        raise NotImplementedError

    def vehicle(self, trip_id: str) -> VehiclePosition | None:
        raise NotImplementedError


class LegacyMenobusz(RealtimeSource):
    """Legacy menőBusz API, as used by github.com/krisz768/BetterTuke.

    POST form to android/v3/handler.php; responses are '|'-separated lines.
      SMART=<stop>  -> upcoming arrivals at stop
      BLATE=<trip>  -> current vehicle position for a trip
      ONWAY=<trip>  -> whether a trip has started
    IDs are from the API's own track.zip DB, not necessarily GTFS IDs.
    """

    def __init__(self, base: str = "http://menobusz.tukebusz.hu/", timeout: float = 8):
        self.base = base
        self.timeout = timeout

    def _post(self, **params) -> list[list[str]]:
        req = Request(
            self.base + "android/v3/handler.php",
            data=urlencode(params).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urlopen(req, timeout=self.timeout) as r:
            body = r.read().decode("utf-8", "replace")
        return [line.split("|") for line in body.splitlines() if line.strip()]

    def health(self):
        try:
            with urlopen(self.base + "android/update.php?id=2", timeout=self.timeout) as r:
                return True, f"HTTP {r.status}: {r.read(200)!r}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def arrivals(self, stop_id):
        return [
            Arrival(p[0], p[1], p[2], int(p[5]), int(p[7]), p[8] == "1")
            for p in self._post(SMART=stop_id)
        ]

    def vehicle(self, trip_id):
        rows = self._post(BLATE=trip_id)
        if not rows:
            return None
        p = rows[-1]
        return VehiclePosition(
            plate=p[0], stop_seq=int(p[4]), stop_id=int(p[5]), at_stop=p[6] == "8",
            lon=float(p[8]), lat=float(p[9]),
            delay_min=int(p[11]), delay_sec=int(p[10]), last_update=p[7],
        )


REALTIME: RealtimeSource = LegacyMenobusz()


# --------------------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, args = argv[1], argv[2:]

    if cmd == "probe":
        ok, msg = REALTIME.health()
        print(f"{type(REALTIME).__name__}: {'UP' if ok else 'DOWN'} - {msg}")
        return 0 if ok else 2

    if cmd == "legacy-stop":
        for a in REALTIME.arrivals(args[0]):
            print(a)
        return 0

    if cmd == "legacy-trip":
        print(REALTIME.vehicle(args[0]))
        return 0

    gtfs = Gtfs(ensure_gtfs())

    if cmd == "stops":
        for s in gtfs.search_stops(" ".join(args)):
            print(f"{s['stop_id']:>8}  {s['stop_name']}  ({s['stop_desc']})  {s['stop_lat']},{s['stop_lon']}")
        return 0

    if cmd == "departures":
        stop_id = args[0]
        stop = gtfs.stops.get(stop_id)
        if not stop:
            print(f"Unknown stop {stop_id}")
            return 1
        print(f"{stop['stop_name']} ({stop['stop_desc']}) - scheduled, next 60 min")
        for dep, line, headsign, trip_id in gtfs.departures(stop_id, datetime.now()):
            print(f"  {dep:%H:%M}  {line:>4}  {headsign}")

        ok, msg = REALTIME.health()
        if not ok:
            print(f"\nRealtime unavailable ({type(REALTIME).__name__}: {msg})")
            return 0
        print("\nRealtime arrivals:")
        for a in REALTIME.arrivals(stop_id):
            print(f"  {a.line:>4}  {a.name}  in {a.remaining_min} min{'  [AT STOP]' if a.at_stop else ''}")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
