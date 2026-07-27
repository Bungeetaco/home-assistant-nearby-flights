"""FlightProcessor behavior: entry/exit diffing, and the full grace-window
matrix - the failure-vs-empty distinction is what keeps a daily rate-limit
outage from wiping the cache and firing false exit events into automations."""
import time

import pytest

import api.flight as flight_mod
from api.event import (
    EVENT_AREA_LANDED,
    EVENT_AREA_TOOK_OFF,
    EVENT_ENTRY,
    EVENT_EXIT,
    EventManager,
)
from api.flight import AREA_STALE_GRACE_S, FlightProcessor
from api.helper import Point
from api.opensky import OpenSkyAuthError


class StubOpenSky:
    """Yields one queued response (list) or raises one queued exception per call."""

    def __init__(self):
        self.queue = []

    def get_states_bbox(self, lamin, lomin, lamax, lomax):
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubAdsbdb:
    def lookup_callsign(self, callsign):
        return None

    def lookup_aircraft(self, icao24):
        return None


def make_state(icao24="abc123", callsign="TST123", on_ground=False, vertical_rate=0.0):
    return {
        "icao24": icao24,
        "callsign": callsign,
        "latitude": 45.1,
        "longitude": -75.1,
        "altitude_m": 3000.0,
        "on_ground": on_ground,
        "velocity_mps": 200.0,
        "true_track": 90.0,
        "vertical_rate_mps": vertical_rate,
        "squawk": "1200",
    }


@pytest.fixture
def clock(monkeypatch):
    state = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: state["t"])
    return state


@pytest.fixture
def processor():
    opensky = StubOpenSky()
    events = EventManager()
    proc = FlightProcessor(
        events, -1, 100000, Point(45.0, -75.0), opensky, StubAdsbdb(),
        (44.0, -76.0, 46.0, -74.0),
    )
    return proc, opensky, events


def fired(events):
    out = []
    events.fire_events("test", out.append)
    return out


class TestEntryExit:
    def test_first_cycle_counts_everything_as_entered(self, clock, processor):
        proc, opensky, events = processor
        opensky.queue.append([make_state("aaa111"), make_state("bbb222")])
        proc.update_flights_in_area()
        assert {f["id"] for f in proc.in_area_list} == {"aaa111", "bbb222"}
        # First cycle has no previous set to diff against - no events yet.
        assert fired(events) == []

    def test_exit_is_detected_and_fired(self, clock, processor):
        proc, opensky, events = processor
        opensky.queue.append([make_state("aaa111"), make_state("bbb222")])
        proc.update_flights_in_area()
        fired(events)
        opensky.queue.append([make_state("aaa111")])
        proc.update_flights_in_area()
        assert [f["id"] for f in proc.exited_list] == ["bbb222"]
        assert [e.event for e in fired(events)] == [EVENT_EXIT]

    def test_entered_exited_are_lists_on_soft_fail(self, clock, processor):
        # Regression: the early-return path used to reset these to {} (dict),
        # flipping the sensors' `flights` attribute type.
        proc, opensky, events = processor
        opensky.queue.append([make_state("aaa111")])
        proc.update_flights_in_area()
        opensky.queue.append(RuntimeError("boom"))
        proc.update_flights_in_area()
        assert proc.entered_list == []
        assert proc.exited_list == []
        assert isinstance(proc.entered_list, list)

    def test_takeoff_and_landing_events(self, clock, processor):
        proc, opensky, events = processor
        opensky.queue.append([make_state("aaa111", on_ground=True)])
        proc.update_flights_in_area()
        fired(events)
        opensky.queue.append([make_state("aaa111", on_ground=False)])
        proc.update_flights_in_area()
        assert EVENT_AREA_TOOK_OFF in [e.event for e in fired(events)]
        opensky.queue.append([make_state("aaa111", on_ground=True)])
        proc.update_flights_in_area()
        assert EVENT_AREA_LANDED in [e.event for e in fired(events)]


class TestGraceWindow:
    def _warm(self, proc, opensky):
        opensky.queue.append([make_state("aaa111")])
        proc.update_flights_in_area()
        assert not proc.area_stale

    def test_failure_within_grace_serves_cache(self, clock, processor):
        proc, opensky, events = processor
        self._warm(proc, opensky)
        clock["t"] += AREA_STALE_GRACE_S - 1
        opensky.queue.append(RuntimeError("network down"))
        proc.update_flights_in_area()
        assert proc.area_stale
        assert [f["id"] for f in proc.in_area_list] == ["aaa111"]
        assert fired(events) == []  # crucially: no false exit events

    def test_failure_past_grace_raises_and_keeps_cache(self, clock, processor):
        proc, opensky, events = processor
        self._warm(proc, opensky)
        clock["t"] += AREA_STALE_GRACE_S + 1
        opensky.queue.append(RuntimeError("network down"))
        with pytest.raises(RuntimeError):
            proc.update_flights_in_area()
        # A failure is NOT evidence of an empty sky: cache intact, no exits.
        assert [f["id"] for f in proc.in_area_list] == ["aaa111"]
        assert proc.area_stale
        assert fired(events) == []

    def test_failure_with_no_cache_raises(self, clock, processor):
        proc, opensky, events = processor
        opensky.queue.append(RuntimeError("cold start failure"))
        with pytest.raises(RuntimeError):
            proc.update_flights_in_area()

    def test_empty_within_grace_serves_cache(self, clock, processor):
        proc, opensky, events = processor
        self._warm(proc, opensky)
        clock["t"] += 10
        opensky.queue.append([])
        proc.update_flights_in_area()
        assert proc.area_stale
        assert [f["id"] for f in proc.in_area_list] == ["aaa111"]
        assert fired(events) == []

    def test_empty_past_grace_is_trusted_and_fires_exits(self, clock, processor):
        proc, opensky, events = processor
        self._warm(proc, opensky)
        clock["t"] += AREA_STALE_GRACE_S + 1
        opensky.queue.append([])
        proc.update_flights_in_area()
        assert not proc.area_stale
        assert proc.in_area_list == []
        assert [e.event for e in fired(events)] == [EVENT_EXIT]

    def test_recovery_clears_stale(self, clock, processor):
        proc, opensky, events = processor
        self._warm(proc, opensky)
        clock["t"] += 10
        opensky.queue.append(RuntimeError("blip"))
        proc.update_flights_in_area()
        assert proc.area_stale
        opensky.queue.append([make_state("aaa111")])
        proc.update_flights_in_area()
        assert not proc.area_stale

    def test_auth_error_propagates_even_within_grace(self, clock, processor):
        proc, opensky, events = processor
        self._warm(proc, opensky)
        clock["t"] += 10
        opensky.queue.append(OpenSkyAuthError("bad credentials"))
        with pytest.raises(OpenSkyAuthError):
            proc.update_flights_in_area()
        assert [f["id"] for f in proc.in_area_list] == ["aaa111"]

    def test_set_in_area_seeds_grace_window(self, clock, processor):
        proc, opensky, events = processor
        restored = {"aaa111": {"id": "aaa111", "closest_distance": 5.0, "on_ground": False}}
        proc.set_in_area(dict(restored))
        opensky.queue.append(RuntimeError("first post-restart poll blocked"))
        proc.update_flights_in_area()
        assert proc.area_stale
        assert [f["id"] for f in proc.in_area_list] == ["aaa111"]


class TestEventManager:
    def test_fire_copies_and_does_not_mutate_flight_dicts(self):
        events = EventManager()
        flight = {"id": "aaa111"}
        events.add_events(EVENT_ENTRY, [flight])
        seen = []
        events.fire_events("MyDevice", seen.append)
        assert seen[0].data["tracked_by_device"] == "MyDevice"
        assert "tracked_by_device" not in flight  # shared dict untouched

    def test_queue_cleared_even_if_callback_raises(self):
        events = EventManager()
        events.add_events(EVENT_ENTRY, [{"id": "a"}, {"id": "b"}])

        def explode(event):
            raise ValueError("subscriber blew up")

        with pytest.raises(ValueError):
            events.fire_events("dev", explode)
        # Next cycle must not re-fire already-delivered events.
        seen = []
        events.fire_events("dev", seen.append)
        assert seen == []


def test_helicopter_detection():
    assert flight_mod.is_helicopter({"callsign": "POLICE1"})
    assert flight_mod.is_helicopter({"aircraft_model": "Eurocopter EC135"})
    assert flight_mod.is_helicopter({"aircraft_code": "R44", "callsign": None})
    assert not flight_mod.is_helicopter({"callsign": "ACA123", "aircraft_model": "Boeing 737"})
