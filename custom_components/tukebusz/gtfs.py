"""Pure-Python GTFS store and timetable queries for Tüke Busz (Pécs).

No Home Assistant imports here, so the module can be unit-tested and used
from plain scripts.

GTFS times are seconds relative to "noon minus 12h" of the service day and
may exceed 24:00:00 (trips running past midnight). All public query results
use timezone-aware datetimes in the feed's timezone.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, NamedTuple
from zoneinfo import ZoneInfo

GTFS_URL = "https://mobilitas.biokom.hu/gtfs/"
DEFAULT_TZ = "Europe/Budapest"
DAY_S = 86400


class Stop(NamedTuple):
    id: str
    name: str
    desc: str
    lat: float
    lon: float


class Route(NamedTuple):
    id: str
    line: str
    name: str
    color: str
    text_color: str


class Trip(NamedTuple):
    id: str
    route_id: str
    service_id: str
    headsign: str


class StopTime(NamedTuple):
    stop_id: str
    arr: int  # seconds since service-day start
    dep: int
    headsign: str
    pickup: bool  # boarding allowed
    dropoff: bool  # alighting allowed


@dataclass
class _Calendar:
    weekdays: tuple[bool, ...]  # monday..sunday
    start: str  # YYYYMMDD
    end: str


def parse_hms(value: str) -> int:
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _natural_key(text: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


class GtfsStore:
    """In-memory index of one GTFS feed."""

    def __init__(self) -> None:
        self.tz = ZoneInfo(DEFAULT_TZ)
        self.stops: dict[str, Stop] = {}
        self.routes: dict[str, Route] = {}
        self.trips: dict[str, Trip] = {}
        self.trip_stops: dict[str, list[StopTime]] = {}
        self.stop_trips: dict[str, list[tuple[str, int]]] = {}
        self.stop_groups: dict[str, list[str]] = {}
        self._calendar: dict[str, _Calendar] = {}
        self._exceptions: dict[str, dict[str, bool]] = {}  # date -> service -> added?
        self._services_cache: dict[date, frozenset[str]] = {}

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, source: str | Path | zipfile.ZipFile) -> "GtfsStore":
        """Load from a GTFS zip file (or a directory of .txt files)."""
        store = cls()
        path = Path(source) if not isinstance(source, zipfile.ZipFile) else None
        if path is not None and path.is_dir():
            read = lambda name: (path / name).read_text("utf-8-sig") if (path / name).exists() else None  # noqa: E731
            store._load(read)
            return store
        zf = source if isinstance(source, zipfile.ZipFile) else zipfile.ZipFile(path)
        with zf:
            names = set(zf.namelist())
            store._load(lambda name: zf.read(name).decode("utf-8-sig") if name in names else None)
        return store

    def _load(self, read) -> None:
        def rows(name: str) -> Iterable[dict[str, str]]:
            text = read(name)
            return csv.DictReader(io.StringIO(text)) if text else []

        for a in rows("agency.txt"):
            if a.get("agency_timezone"):
                self.tz = ZoneInfo(a["agency_timezone"])
                break

        for s in rows("stops.txt"):
            if s.get("location_type", "0") not in ("", "0"):
                continue
            stop = Stop(s["stop_id"], s["stop_name"].strip(), (s.get("stop_desc") or "").strip(),
                        float(s.get("stop_lat") or 0), float(s.get("stop_lon") or 0))
            self.stops[stop.id] = stop
            self.stop_groups.setdefault(stop.name, []).append(stop.id)

        for r in rows("routes.txt"):
            self.routes[r["route_id"]] = Route(
                r["route_id"], r.get("route_short_name") or r["route_id"], r.get("route_long_name") or "",
                (r.get("route_color") or "3F51B5").upper(), (r.get("route_text_color") or "FFFFFF").upper())

        for t in rows("trips.txt"):
            self.trips[t["trip_id"]] = Trip(t["trip_id"], t["route_id"], t["service_id"], t.get("trip_headsign") or "")

        raw: dict[str, list[tuple[int, StopTime]]] = {}
        for st in rows("stop_times.txt"):
            trip_id = st["trip_id"]
            if trip_id not in self.trips or st["stop_id"] not in self.stops:
                continue
            arr_txt = st.get("arrival_time") or st.get("departure_time")
            dep_txt = st.get("departure_time") or arr_txt
            if not arr_txt:
                continue  # untimed stop, not used by Tüke feed
            raw.setdefault(trip_id, []).append((int(st["stop_sequence"]), StopTime(
                st["stop_id"], parse_hms(arr_txt), parse_hms(dep_txt), (st.get("stop_headsign") or "").strip(),
                st.get("pickup_type", "0") != "1", st.get("drop_off_type", "0") != "1")))
        for trip_id, items in raw.items():
            items.sort(key=lambda x: x[0])
            seq = [x[1] for x in items]
            self.trip_stops[trip_id] = seq
            for idx, st in enumerate(seq):
                self.stop_trips.setdefault(st.stop_id, []).append((trip_id, idx))

        for c in rows("calendar.txt"):
            days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            self._calendar[c["service_id"]] = _Calendar(tuple(c[d] == "1" for d in days), c["start_date"], c["end_date"])
        for e in rows("calendar_dates.txt"):
            self._exceptions.setdefault(e["date"], {})[e["service_id"]] = e["exception_type"] == "1"

    # ------------------------------------------------------------------ calendar / time

    def active_services(self, day: date) -> frozenset[str]:
        cached = self._services_cache.get(day)
        if cached is not None:
            return cached
        ymd = day.strftime("%Y%m%d")
        active = {sid for sid, c in self._calendar.items() if c.start <= ymd <= c.end and c.weekdays[day.weekday()]}
        for sid, added in self._exceptions.get(ymd, {}).items():
            (active.add if added else active.discard)(sid)
        result = frozenset(active)
        self._services_cache[day] = result
        return result

    def feed_range(self) -> tuple[date | None, date | None]:
        dates = [c.start for c in self._calendar.values()] + [c.end for c in self._calendar.values()]
        dates += [d for d, ex in self._exceptions.items() if any(ex.values())]
        if not dates:
            return None, None
        parse = lambda s: datetime.strptime(s, "%Y%m%d").date()  # noqa: E731
        return parse(min(dates)), parse(max(dates))

    def service_dt(self, day: date, seconds: int) -> datetime:
        """Aware datetime for a GTFS time on a service day (noon - 12h rule)."""
        noon = datetime.combine(day, time(12), self.tz)
        base_utc = noon.astimezone(timezone.utc) - timedelta(hours=12)
        return (base_utc + timedelta(seconds=seconds)).astimezone(self.tz)

    # ------------------------------------------------------------------ lookups

    def resolve_stop(self, value: str | Iterable[str]) -> list[str]:
        """Stop id, stop name (all platforms) or a list of those -> stop ids."""
        values = [value] if isinstance(value, str) else list(value)
        ids: list[str] = []
        for v in values:
            v = str(v).strip()
            if v in self.stops:
                ids.append(v)
            elif v in self.stop_groups:
                ids.extend(self.stop_groups[v])
            else:
                raise KeyError(f"Unknown stop: {v}")
        return list(dict.fromkeys(ids))

    def resolve_route(self, value: str) -> Route:
        value = str(value).strip()
        if value in self.routes:
            return self.routes[value]
        for r in self.routes.values():
            if r.line.lower() == value.lower():
                return r
        raise KeyError(f"Unknown line: {value}")

    def stop_label(self, stop_ids: list[str]) -> str:
        names = list(dict.fromkeys(self.stops[s].name for s in stop_ids))
        if len(stop_ids) == 1:
            s = self.stops[stop_ids[0]]
            return f"{s.name} ({s.desc})" if s.desc else s.name
        return " / ".join(names)

    def _headsign(self, trip: Trip, st: StopTime) -> str:
        if st.headsign:
            return st.headsign
        if trip.headsign:
            return trip.headsign
        return self.stops[self.trip_stops[trip.id][-1].stop_id].name

    def _line_ok(self, trip: Trip, lines: set[str] | None) -> bool:
        if not lines:
            return True
        r = self.routes.get(trip.route_id)
        return r is not None and (r.line.lower() in lines or r.id.lower() in lines)

    @staticmethod
    def _norm_lines(lines: Iterable[str] | str | None) -> set[str] | None:
        if not lines:
            return None
        if isinstance(lines, str):
            lines = re.split(r"[,\s]+", lines)
        result = {str(x).strip().lower() for x in lines if str(x).strip()}
        return result or None

    def _route_info(self, trip: Trip) -> dict:
        r = self.routes.get(trip.route_id) or Route(trip.route_id, trip.route_id, "", "3F51B5", "FFFFFF")
        return {"line": r.line, "route_id": r.id, "route_name": r.name,
                "color": f"#{r.color}", "text_color": f"#{r.text_color}"}

    # ------------------------------------------------------------------ queries

    def next_journeys(self, origin: str | list[str], destination: str | list[str] | None,
                      now: datetime, count: int = 5, lines: Iterable[str] | str | None = None,
                      horizon_hours: int = 36) -> list[dict]:
        """Next `count` trips leaving origin (and later reaching destination)."""
        origin_ids = set(self.resolve_stop(origin))
        dest_ids = set(self.resolve_stop(destination)) if destination else None
        line_set = self._norm_lines(lines)
        now = now.astimezone(self.tz)
        limit = now + timedelta(hours=horizon_hours)

        candidate_trips = {tid for sid in origin_ids for tid, _ in self.stop_trips.get(sid, [])}
        results: list[dict] = []
        day0 = now.date()
        for offset in range(-1, horizon_hours // 24 + 2):
            day = day0 + timedelta(days=offset)
            services = self.active_services(day)
            for tid in candidate_trips:
                trip = self.trips[tid]
                if trip.service_id not in services or not self._line_ok(trip, line_set):
                    continue
                for o_idx, d_idx in self._legs(self.trip_stops[tid], origin_ids, dest_ids):
                    seq = self.trip_stops[tid]
                    dep = self.service_dt(day, seq[o_idx].dep)
                    if dep < now or dep > limit:
                        continue
                    results.append(self._journey(trip, day, o_idx, d_idx, dep))
                    break  # first valid leg of this trip on this day
        results.sort(key=lambda j: (j["departure"], j["line"]))
        return results[:count]

    @staticmethod
    def _legs(seq: list[StopTime], origin_ids: set[str], dest_ids: set[str] | None):
        """Yield (origin_idx, dest_idx|None) pairs in trip order.

        With a destination, the latest boarding at the origin group before
        reaching the destination is used (shortest ride on loop lines).
        """
        if dest_ids is None:
            for i, st in enumerate(seq[:-1]):
                if st.stop_id in origin_ids and st.pickup:
                    yield i, None
            return
        last_origin = None
        for i, st in enumerate(seq):
            if last_origin is not None and st.stop_id in dest_ids and st.dropoff and st.stop_id not in origin_ids:
                yield last_origin, i
                last_origin = None
            if st.stop_id in origin_ids and st.pickup and i < len(seq) - 1:
                last_origin = i

    def _journey(self, trip: Trip, day: date, o_idx: int, d_idx: int | None, dep: datetime) -> dict:
        seq = self.trip_stops[trip.id]
        o = seq[o_idx]
        origin_stop = self.stops[o.stop_id]
        item = {
            **self._route_info(trip),
            "trip_id": trip.id,
            "headsign": self._headsign(trip, o),
            "departure": dep,
            "origin_stop_id": o.stop_id,
            "origin_name": origin_stop.name,
            "origin_platform": origin_stop.desc,
            "arrival": None,
            "destination_stop_id": None,
            "destination_name": None,
            "destination_platform": None,
            "duration_min": None,
        }
        if d_idx is not None:
            d = seq[d_idx]
            arr = self.service_dt(day, d.arr)
            dest_stop = self.stops[d.stop_id]
            item.update(arrival=arr, destination_stop_id=d.stop_id, destination_name=dest_stop.name,
                        destination_platform=dest_stop.desc,
                        duration_min=round((arr - dep).total_seconds() / 60))
        return item

    def _day_window(self, day: date, start: time | None, end: time | None) -> tuple[datetime, datetime]:
        lo = datetime.combine(day, start or time(0), self.tz)
        hi = datetime.combine(day, end, self.tz) if end else datetime.combine(day + timedelta(days=1), time(0), self.tz)
        if hi <= lo:  # window across midnight, e.g. 22:00-02:00
            hi += timedelta(days=1)
        return lo, hi

    def stop_timetable(self, stop: str | list[str], day: date, start: time | None = None,
                       end: time | None = None, lines: Iterable[str] | str | None = None) -> list[dict]:
        """All departures from a stop (or stop group) on a day / within a window."""
        stop_ids = set(self.resolve_stop(stop))
        line_set = self._norm_lines(lines)
        lo, hi = self._day_window(day, start, end)
        out = []
        for sday in (day - timedelta(days=1), day, day + timedelta(days=1)):
            services = self.active_services(sday)
            for sid in stop_ids:
                for tid, idx in self.stop_trips.get(sid, []):
                    trip = self.trips[tid]
                    seq = self.trip_stops[tid]
                    if trip.service_id not in services or idx == len(seq) - 1 or not seq[idx].pickup:
                        continue
                    if not self._line_ok(trip, line_set):
                        continue
                    dep = self.service_dt(sday, seq[idx].dep)
                    if not lo <= dep < hi:
                        continue
                    st = self.stops[sid]
                    out.append({**self._route_info(trip), "trip_id": tid, "departure": dep,
                                "time": dep.strftime("%H:%M"), "headsign": self._headsign(trip, seq[idx]),
                                "stop_id": sid, "stop_name": st.name, "platform": st.desc})
        out.sort(key=lambda d: (d["departure"], _natural_key(d["line"])))
        return out

    def line_timetable(self, line: str, day: date, start: time | None = None, end: time | None = None,
                       direction: str | None = None) -> dict:
        """Grid of stop x trip departure times for one line, grouped by direction (terminus)."""
        route = self.resolve_route(line)
        lo, hi = self._day_window(day, start, end)
        groups: dict[str, list[tuple[date, Trip]]] = {}
        for sday in (day - timedelta(days=1), day, day + timedelta(days=1)):
            services = self.active_services(sday)
            for trip in self.trips.values():
                if trip.route_id != route.id or trip.service_id not in services:
                    continue
                seq = self.trip_stops.get(trip.id)
                if not seq:
                    continue
                first = self.service_dt(sday, seq[0].dep)
                last = self.service_dt(sday, seq[-1].arr)
                if last < lo or first >= hi:
                    continue
                terminus = self.stops[seq[-1].stop_id].name
                if direction and terminus.lower() != direction.lower():
                    continue
                groups.setdefault(terminus, []).append((sday, trip))

        directions = []
        for terminus, trips in sorted(groups.items()):
            trips.sort(key=lambda x: self.service_dt(x[0], self.trip_stops[x[1].id][0].dep))
            order = self._merge_stop_order([[st.stop_id for st in self.trip_stops[t.id]] for _, t in trips])
            pos = {sid: i for i, sid in enumerate(order)}
            rows = []
            for sday, trip in trips:
                times: list[str | None] = [None] * len(order)
                for st in self.trip_stops[trip.id]:
                    i = pos[st.stop_id]
                    if times[i] is None:
                        times[i] = self.service_dt(sday, st.dep).strftime("%H:%M")
                first_stop = self.stops[self.trip_stops[trip.id][0].stop_id].name
                rows.append({"trip_id": trip.id, "from": first_stop, "times": times,
                             "start": self.service_dt(sday, self.trip_stops[trip.id][0].dep).isoformat()})
            directions.append({
                "terminus": terminus,
                "stops": [{"stop_id": s, "name": self.stops[s].name, "platform": self.stops[s].desc} for s in order],
                "trips": rows,
            })
        return {**self._route_info(Trip("", route.id, "", "")), "date": day.isoformat(),
                "directions": directions}

    @staticmethod
    def _merge_stop_order(sequences: list[list[str]]) -> list[str]:
        """Merge stop sequences into one order, longest first, inserting extras after their predecessor."""
        order: list[str] = []
        for seq in sorted(sequences, key=len, reverse=True):
            prev = -1
            for sid in seq:
                if sid in order:
                    prev = order.index(sid)
                else:
                    prev += 1
                    order.insert(prev, sid)
        return order

    def metadata(self) -> dict:
        start, end = self.feed_range()
        stops = []
        for name, ids in sorted(self.stop_groups.items(), key=lambda kv: kv[0].lower()):
            stops.append({"name": name, "platforms": [
                {"stop_id": i, "platform": self.stops[i].desc, "lat": self.stops[i].lat, "lon": self.stops[i].lon}
                for i in sorted(ids)]})
        routes = [{"route_id": r.id, "line": r.line, "name": r.name, "color": f"#{r.color}",
                   "text_color": f"#{r.text_color}",
                   "directions": self.line_directions(r.id)}
                  for r in sorted(self.routes.values(), key=lambda r: _natural_key(r.line))]
        return {"feed_start": start.isoformat() if start else None, "feed_end": end.isoformat() if end else None,
                "stops": stops, "routes": routes}

    def line_directions(self, route_id: str) -> list[str]:
        return sorted({self.stops[self.trip_stops[t.id][-1].stop_id].name
                       for t in self.trips.values() if t.route_id == route_id and self.trip_stops.get(t.id)})


def serialize_journey(j: dict, now: datetime) -> dict:
    """JSON-friendly journey with exact and relative times."""
    minutes = max(0, int((j["departure"] - now).total_seconds() // 60))
    out = {k: v for k, v in j.items() if k not in ("departure", "arrival")}
    out.update(
        departure=j["departure"].isoformat(),
        departure_str=j["departure"].strftime("%Y-%m-%d %H:%M"),
        arrival=j["arrival"].isoformat() if j["arrival"] else None,
        arrival_str=j["arrival"].strftime("%Y-%m-%d %H:%M") if j["arrival"] else None,
        minutes_until=minutes,
        relative=relative_hu(minutes),
    )
    return out


def serialize_departure(d: dict) -> dict:
    return {**d, "departure": d["departure"].isoformat()}


def relative_hu(minutes: int) -> str:
    if minutes <= 0:
        return "most"
    if minutes < 60:
        return f"{minutes} perc múlva"
    h, m = divmod(minutes, 60)
    return f"{h} óra {m} perc múlva" if m else f"{h} óra múlva"
