"""Tests for custom_components/tukebusz/gtfs.py (no Home Assistant needed).

Run:  python -m unittest discover tests     (or: pytest tests)
"""
import importlib.util
import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tukebusz_gtfs", ROOT / "custom_components/tukebusz/gtfs.py")
gtfs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gtfs
_spec.loader.exec_module(gtfs)

STORE = gtfs.GtfsStore.load(ROOT / "tests/fixtures/mini_gtfs")
TZ = STORE.tz
TUE = date(2026, 11, 10)
MON_HOLIDAY = date(2026, 11, 9)  # WD removed, WE added via calendar_dates


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), TZ)


class CalendarTests(unittest.TestCase):
    def test_regular_weekday(self):
        self.assertEqual(STORE.active_services(TUE), {"WD"})

    def test_exceptions(self):
        self.assertEqual(STORE.active_services(MON_HOLIDAY), {"WE"})

    def test_weekend(self):
        self.assertEqual(STORE.active_services(date(2026, 11, 14)), {"WE"})

    def test_service_dt_on_dst_change_day(self):
        # 2026-10-25: CEST -> CET. Noon-minus-12h rule keeps 12:00 at 12:00 local.
        self.assertEqual(STORE.service_dt(date(2026, 10, 25), 12 * 3600).strftime("%H:%M"), "12:00")

    def test_feed_range(self):
        self.assertEqual(STORE.feed_range(), (date(2026, 1, 1), date(2026, 12, 31)))


class LookupTests(unittest.TestCase):
    def test_stop_group_and_platform(self):
        self.assertEqual(sorted(STORE.resolve_stop("Alfa")), ["A1", "A2"])
        self.assertEqual(STORE.resolve_stop("A2"), ["A2"])
        with self.assertRaises(KeyError):
            STORE.resolve_stop("Nincs ilyen")

    def test_route_by_line_number(self):
        self.assertEqual(STORE.resolve_route("2").id, "R2")


class JourneyTests(unittest.TestCase):
    def test_a_to_c_only_forward_and_boardable(self):
        js = STORE.next_journeys("Alfa", "Céza", at(TUE, 7), count=5)
        # T2 runs C->A (wrong direction), T5 has no pickup at A1; next day's T1 follows.
        self.assertEqual([j["trip_id"] for j in js], ["T1", "T1"])
        j = js[0]
        self.assertEqual(j["departure"], at(TUE, 8))
        self.assertEqual(j["arrival"], at(TUE, 8, 20))
        self.assertEqual(j["duration_min"], 20)
        self.assertEqual(j["line"], "1")
        self.assertEqual(j["headsign"], "Céza")
        self.assertEqual(j["origin_platform"], "1-es kocsiállás")

    def test_after_midnight_trip(self):
        j = STORE.next_journeys("Alfa", "Béta", at(TUE, 23), count=1)[0]
        self.assertEqual(j["trip_id"], "T3")
        self.assertEqual(j["departure"], at(TUE, 23, 50))
        self.assertEqual(j["arrival"], at(date(2026, 11, 11), 0, 5))

    def test_previous_service_day_carryover(self):
        j = STORE.next_journeys("Béta", "Delta", at(date(2026, 11, 11), 0, 1), count=1)[0]
        self.assertEqual(j["trip_id"], "T3")
        self.assertEqual(j["arrival"], at(date(2026, 11, 11), 0, 20))

    def test_holiday_uses_weekend_service(self):
        j = STORE.next_journeys("Alfa", "Céza", at(MON_HOLIDAY, 7), count=1)[0]
        self.assertEqual((j["trip_id"], j["departure"]), ("T4", at(MON_HOLIDAY, 10)))

    def test_no_destination_and_count(self):
        js = STORE.next_journeys("Alfa", None, at(TUE, 7), count=2)
        self.assertEqual([j["trip_id"] for j in js], ["T1", "T3"])
        self.assertIsNone(js[0]["arrival"])

    def test_line_filter(self):
        js = STORE.next_journeys("Alfa", None, at(TUE, 7), count=5, lines="2")
        self.assertTrue(js and all(j["line"] == "2" for j in js))

    def test_serialize(self):
        j = STORE.next_journeys("Alfa", "Céza", at(TUE, 7, 55), count=1)[0]
        s = gtfs.serialize_journey(j, at(TUE, 7, 55))
        self.assertEqual(s["minutes_until"], 5)
        self.assertEqual(s["relative"], "5 perc múlva")
        self.assertEqual(s["departure_str"], "2026-11-10 08:00")
        self.assertEqual(s["arrival_str"], "2026-11-10 08:20")


class TimetableTests(unittest.TestCase):
    def test_stop_whole_day(self):
        deps = STORE.stop_timetable("Alfa", TUE)
        # T2 terminates at A2 (no departure), T5 has no pickup.
        self.assertEqual([(d["time"], d["trip_id"]) for d in deps], [("08:00", "T1"), ("23:50", "T3")])

    def test_stop_window(self):
        deps = STORE.stop_timetable("Alfa", TUE, time(7), time(9))
        self.assertEqual([d["trip_id"] for d in deps], ["T1"])

    def test_stop_includes_previous_day_after_midnight(self):
        deps = STORE.stop_timetable("Béta", date(2026, 11, 11))
        self.assertEqual([(d["time"], d["trip_id"]) for d in deps], [("00:05", "T3"), ("08:10", "T1"), ("09:10", "T2"), ("12:10", "T5")])

    def test_line_grid(self):
        res = STORE.line_timetable("1", TUE)
        dirs = {d["terminus"]: d for d in res["directions"]}
        self.assertEqual(set(dirs), {"Céza", "Alfa"})
        to_c = dirs["Céza"]
        self.assertEqual([s["stop_id"] for s in to_c["stops"]], ["A1", "B", "C"])
        self.assertEqual([t["times"] for t in to_c["trips"]], [["08:00", "08:10", "08:20"], ["12:00", "12:10", "12:20"]])

    def test_line_window_and_direction(self):
        res = STORE.line_timetable("1", TUE, time(8, 30), time(10), direction="Alfa")
        self.assertEqual([t["trip_id"] for d in res["directions"] for t in d["trips"]], ["T2"])

    def test_metadata(self):
        m = STORE.metadata()
        self.assertEqual([s["name"] for s in m["stops"]], ["Alfa", "Béta", "Céza", "Delta"])
        self.assertEqual(m["routes"][0]["directions"], ["Alfa", "Céza"])


class RelativeTests(unittest.TestCase):
    def test_relative_hu(self):
        self.assertEqual(gtfs.relative_hu(0), "most")
        self.assertEqual(gtfs.relative_hu(5), "5 perc múlva")
        self.assertEqual(gtfs.relative_hu(60), "1 óra múlva")
        self.assertEqual(gtfs.relative_hu(75), "1 óra 15 perc múlva")


if __name__ == "__main__":
    unittest.main()
