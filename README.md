<p align="center">
  <img src="custom_components/nearby_flights/brand/icon@2x.png" width="96" height="96" alt="Nearby Flights icon">
</p>

# Nearby Flights for Home Assistant

A Home Assistant custom integration that reports aircraft currently flying near a
configured location. I originally forked this from
[AlexandrErohin/home-assistant-flightradar24](https://github.com/AlexandrErohin/home-assistant-flightradar24)
(this repo still carries that project's original commit history); as of v2.0.0 it no
longer depends on FlightRadar24 at all, and as of 2026-07-20 I've made it a standalone
project rather than a GitHub fork of that repo (see "Project history" below).

## Why I built this

FlightRadar24's public web API silently soft-blocks scraping-style traffic: instead of
an error, it returns a valid HTTP 200 with an empty flight list, so I couldn't tell
"blocked" apart from "genuinely no traffic right now." That happened to me repeatedly on
my live install, so I replaced FlightRadar24 entirely with:

- **[OpenSky Network](https://opensky-network.org/)**: live aircraft positions
  (state vectors) via its `states/all` bounding-box API. Free registered accounts get
  4,000 API credits/day, which comfortably covers a 60s polling interval.
- **[adsbdb.com](https://www.adsbdb.com/)**: free, no-auth route (origin/destination/
  airline) and aircraft-type/registration lookups by callsign/ICAO24, used to enrich the
  raw OpenSky position data.

As part of that switch, I dropped the FlightRadar24-only features that had no
OpenSky/adsbdb equivalent: Most Tracked flights, airport arrivals/departures schedule &
delay stats, and tracking individual flights/aircraft by registration or flight number
(which also removed the `device_tracker` platform and the add/remove-track text
entities). They're gone as of v2.0.0. I want to be clear that I'm not dropping them
because they're bad features. They just weren't part of how I use this integration, and
I had no free-tier data source left to back them once FlightRadar24 was out of the
picture. If you relied on any of that, the original
[AlexandrErohin/home-assistant-flightradar24](https://github.com/AlexandrErohin/home-assistant-flightradar24)
still has it.

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

1. **Location**: latitude/longitude (defaults to your Home Assistant location) and a
   radius in meters to search around it.
2. **Update interval**: how often (seconds) to poll OpenSky. Keep this comfortably
   above what your daily API credit budget allows (registered accounts: 4,000/day).
3. **OpenSky client ID/secret**: register a free account at
   [opensky-network.org](https://opensky-network.org/) and create an API client under
   your account settings to get these.
4. **Min/max altitude** (optional, editable later via the integration's Options): filter
   out aircraft outside this altitude band.

## Entities

| Entity | Description |
|---|---|
| `sensor.nearby_flights_current_in_area` | Number of aircraft currently in the configured area; `flights` attribute holds the full enriched list (position, altitude, speed, route, airline, aircraft type, and a derived `status` of `Departing`/`Landing`/`Climbing`/`Descending`/`Cruising`/`On Ground`) |
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

## Dashboard card

`www/flight-panel-card.js` is a custom Lovelace card (radar map + scrolling ticker list)
I built for this integration's `sensor.nearby_flights_current_in_area` entity. It's not
required to use the integration, but it's how I actually look at the data on my own
dashboard.

It mounts `www/home-assistant-flightradar24-card-square.js` as a child element for the
radar map pane. **That file is a hand-patched fork of
[Springvar/home-assistant-flightradar24-card](https://github.com/Springvar/home-assistant-flightradar24-card)**.
Full credit to Springvar for the original map/radar rendering it's built on; my patches
are limited to renaming its custom element (`flightradar24-card` →
`nearby-flights-map-card`, so it no longer collides with anything FlightRadar24-branded)
and a CSS fix to make the radar map square instead of circular.
`www/home-assistant-flightradar24-card.js` is Springvar's original, unpatched file, kept
only as a reference baseline for diffing against upstream.

### Installing the card

HACS's "plugin"/dashboard category needs a repo-name/filename match that doesn't apply
here, so install manually rather than via HACS:

1. Create `<config>/www/community/nearby-flights-card/` and copy both `flight-panel-card.js`
   and `home-assistant-flightradar24-card-square.js` into it (skip the third, unpatched
   file, it's a reference copy, not meant to be loaded).
2. Register both as Lovelace resources (**Settings → Dashboards → ⋮ → Resources → Add
   Resource**, type **JavaScript Module**):
   - `/local/community/nearby-flights-card/flight-panel-card.js`
   - `/local/community/nearby-flights-card/home-assistant-flightradar24-card-square.js`
3. Add the card to a dashboard:

   ```yaml
   type: custom:flight-panel-card
   entity: sensor.nearby_flights_current_in_area
   radius_km: 100   # tune to your actual distance from the nearest airport(s)
   aspect_ratio: 2
   map_radar_size: 100
   ```

   Full list of optional config keys (units, ticker row limits, sort order, backend
   radius auto-grow, polling backoff, etc.) is documented in the comment header at the
   top of `flight-panel-card.js`.

## Project history

This started as a GitHub fork of
[AlexandrErohin/home-assistant-flightradar24](https://github.com/AlexandrErohin/home-assistant-flightradar24)
and diverged heavily as I progressively replaced FlightRadar24 with OpenSky+adsbdb and
then removed it entirely. By v2.0.0 nothing in this repo talks to FlightRadar24 anymore,
so it no longer made sense to keep it linked as a GitHub fork of an integration it isn't
related to anymore. As of 2026-07-20 it's a standalone repository (full original commit
history preserved, GitHub's fork/parent relationship dropped). Full credit to
AlexandrErohin's original project for the foundation I built this on.

## Notes

- If the OpenSky feed fails or returns empty for a cycle, the integration serves the
  last known-good flight list for up to 10 minutes (flagged via the `stale` attribute)
  rather than immediately reporting zero flights (the same soft-block-shaped failure
  mode FlightRadar24 had), just handled defensively regardless of cause. If this persists
  past 20 minutes, a Home Assistant Repair issue is raised.
- `adsbdb` has no live schedule/timing data (only static route info), so
  `time_scheduled_departure`/`time_estimated_arrival`/etc. attributes are always `None`.
  The derived `status` field is the intended replacement for phase-of-flight display -
  `Departing`/`Landing` when a climb/descent is near the ground (below 10,000ft) or
  close to the resolved origin/destination airport, `Climbing`/`Descending` for the
  same vertical motion elsewhere (a mid-route altitude change isn't a takeoff or
  landing), `Cruising` for level flight, `On Ground` when parked/taxiing.
