from typing import Any, Callable
from dataclasses import dataclass

# Names of the events fired on the HA bus. Defined here rather than in
# ..const so the api/ package imports standalone (plain pytest + requests, no
# homeassistant install) - importing ..const would execute the integration's
# __init__.py, which imports homeassistant. The prefix must match DOMAIN in
# const.py, which re-exports these for the HA-side modules.
_EVENT_PREFIX = "nearby_flights"
EVENT_ENTRY = f"{_EVENT_PREFIX}_entry"
EVENT_EXIT = f"{_EVENT_PREFIX}_exit"
EVENT_AREA_LANDED = f"{_EVENT_PREFIX}_area_landed"
EVENT_AREA_TOOK_OFF = f"{_EVENT_PREFIX}_area_took_off"


@dataclass
class Event:
    event: str
    data: dict[str, Any]


class EventManager:
    __slots__ = ('_events',)

    def __init__(self):
        self._events: list[Event] = []

    def add_events(self, event: str, flights: list[dict[str, Any]]) -> None:
        self._events.extend([Event(event, flight) for flight in flights])

    def fire_events(self, device: str, callback: Callable[[Event], None]) -> None:
        # Swap the queue out first so a callback exception can't re-fire
        # already-delivered events on the next cycle, and fire a copy so
        # tracked_by_device doesn't leak into the shared flight dicts that
        # sensor attributes and restore snapshots also reference.
        events, self._events = self._events, []
        for event in events:
            callback(Event(event.event, {**event.data, 'tracked_by_device': device}))
