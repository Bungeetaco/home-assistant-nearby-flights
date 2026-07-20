# Nearby Flights for Home Assistant

A Home Assistant custom integration that reports aircraft currently flying near a
configured location. Originally forked from
[AlexandrErohin/home-assistant-flightradar24](https://github.com/AlexandrErohin/home-assistant-flightradar24)
(this repo carries that project's original commit history); as of v2.0.0 it no longer
depends on FlightRadar24 at all, and as of 2026-07-20 it's a standalone project rather
than a GitHub fork of that repo — see "Project history" below.

## Why this project exists

FlightRadar24's public web API silently soft-blocks scraping-style traffic: instead of
an error, it returns a valid HTTP 200 with an empty flight list, making it impossible to
distinguish "blocked" from "genuinely no traffic right now." This happened repeatedly on
a live install. This project replaces FlightRadar24 entirely with:

- **[OpenSky Network](https://opensky-network.org/)** — live aircraft positions
  (state vectors) via its `states/all` bounding-box API. Free registered accounts get
  4,000 API credits/day, which comfortably covers a 60s polling interval.
- **[adsbdb.com](https://www.adsbdb.com/)** — free, no-auth route (origin/destination/
  airline) and aircraft-type/registration lookups by callsign/ICAO24, used to enrich the
  raw OpenSky position data.

Because of this, the following FlightRadar24-only features (which had no OpenSky/adsbdb
equivalent, and were unused dead weight even before removal) are gone as of v2.0.0: Most
Tracked flights, airport arrivals/departures schedule & delay stats, and tracking
individual flights/aircraft by registration or flight number (which also removed the
`device_tracker` platform and the add/remove-track text entities).

## Installation

### HACS

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=Bungeetaco&repository=home-assistant-nearby-flights&category=integration" target="_blank"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store." /></a>

Add this repository as a custom HACS repository, then install "Nearby Flights".

### Manual

Copy the `custom_components/nearby_flights` directory into your Home Assistant
`custom_components` directory and restart Home Assistant.

## Configuration

Configuration is done entirely through the UI (**Settings → Devices & Services → Add
Integration → Nearby Flights**). You'll need:

1. **Location** — latitude/longitude (defaults to your Home Assistant location) and a
   radius in meters to search around it.
2. **Update interval** — how often (seconds) to poll OpenSky. Keep this comfortably
   above what your daily API credit budget allows (registered accounts: 4,000/day).
3. **OpenSky client ID/secret** — register a free account at
   [opensky-network.org](https://opensky-network.org/) and create an API client under
   your account settings to get these.
4. **Min/max altitude** (optional, editable later via the integration's Options) — filter
   out aircraft outside this altitude band.

## Entities

| Entity | Description |
|---|---|
| `sensor.nearby_flights_current_in_area` | Number of aircraft currently in the configured area; `flights` attribute holds the full enriched list (position, altitude, speed, route, airline, aircraft type, and a derived `status` of `Departing`/`Landing`/`Cruising`/`On Ground`) |
| `sensor.nearby_flights_entered_area` | Aircraft that entered the area since the last update |
| `sensor.nearby_flights_exited_area` | Aircraft that exited the area since the last update |
| `switch.nearby_flights_api_data_fetching` | Turn polling on/off |

## Events

| Event | Description |
|---|---|
| `nearby_flights_entry` | Flight entered your area |
| `nearby_flights_exit` | Flight exited your area |
| `nearby_flights_area_landed` | Aircraft landed nearby |
| `nearby_flights_area_took_off` | Aircraft took off nearby |

## Project history

This started as a GitHub fork of
[AlexandrErohin/home-assistant-flightradar24](https://github.com/AlexandrErohin/home-assistant-flightradar24)
and diverged heavily as FlightRadar24 was progressively replaced with OpenSky+adsbdb and
then removed entirely — by v2.0.0 nothing in this repo talks to FlightRadar24 anymore, so
it no longer made sense to keep it linked as a GitHub fork of an integration it isn't
related to anymore. As of 2026-07-20 it's a standalone repository (full original commit
history preserved, GitHub's fork/parent relationship dropped). Full credit to
AlexandrErohin's original project for the foundation this was built on.

## Notes

- If the OpenSky feed fails or returns empty for a cycle, the integration serves the
  last known-good flight list for up to 10 minutes (flagged via the `stale` attribute)
  rather than immediately reporting zero flights — the same soft-block-shaped failure
  mode FlightRadar24 had, just handled defensively regardless of cause. If this persists
  past 20 minutes, a Home Assistant Repair issue is raised.
- `adsbdb` has no live schedule/timing data (only static route info), so
  `time_scheduled_departure`/`time_estimated_arrival`/etc. attributes are always `None`.
  The derived `status` field (`Departing`/`Landing`/`Cruising`/`On Ground`, from
  on-ground state + vertical rate) is the intended replacement for phase-of-flight
  display.
