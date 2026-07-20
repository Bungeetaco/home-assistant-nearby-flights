# Setup instructions for an AI agent

This document is written to be followed literally by an AI coding/ops agent
(e.g. Claude Code) with shell and Home Assistant access on the target machine.
Every step includes exact values — don't guess or substitute defaults without
checking the "tune this" notes.

## What this repo is

Two independent pieces:

1. **`custom_components/nearby_flights/`** — a Home Assistant integration, originally
   forked from
   [AlexandrErohin/home-assistant-flightradar24](https://github.com/AlexandrErohin/home-assistant-flightradar24)
   (domain: `nearby_flights`, renamed from `flightradar24`). As of 2026-07-19 it no
   longer talks to that original vendor at all — the in-area feed is backed by
   [OpenSky Network](https://opensky-network.org/) (live positions) + [adsbdb.com](https://www.adsbdb.com/)
   (route/aircraft enrichment), because the original vendor's endpoints repeatedly
   soft-blocked (HTTP 200 with empty data, not an error).
2. **`www/`** — two Lovelace frontend cards:
   - `flight-panel-card.js` — a custom "panel" card (radar map + scrolling ticker
     list) that wraps the card below.
   - `home-assistant-flightradar24-card-square.js` — a patched fork of
     [Springvar/home-assistant-flightradar24-card](https://github.com/Springvar/home-assistant-flightradar24-card)
     (defines the `nearby-flights-map-card` custom element, renamed 2026-07-19 from
     `flightradar24-card`; patched to make the radar map square instead of
     circular). **Required** — the panel card mounts this as a child element
     internally.

Only piece 1 installs via HACS. Piece 2 is a manual copy (see Part 2) — HACS's
"plugin" category requires a JS file whose name matches the repo name (or the
repo name minus a `lovelace-` prefix), which doesn't apply here, so don't try
to add this repo to HACS under the "Dashboard"/plugin category — it won't be
detected correctly.

## Prerequisites

- HACS already installed and working on the target Home Assistant instance.
- Admin access to that Home Assistant instance (UI login or a long-lived
  access token with admin scope for API/WebSocket calls).
- A free [OpenSky Network](https://opensky-network.org/) account, with an API
  client (client ID + secret) created under your account settings — required
  at setup time, not optional. Registered accounts get 4,000 API credits/day.
- **Check for a conflicting existing install first**: if the official
  `AlexandrErohin/home-assistant-flightradar24` integration (or any other
  `flightradar24`/`nearby_flights`-domain integration) is already installed,
  remove/uninstall it before adding this one — they can't coexist under the
  same domain.

## Part 1 — Backend integration (via HACS)

1. In Home Assistant: **Settings → Devices & Services → HACS → Integrations →
   ⋮ (top right) → Custom repositories**.
2. Add repository URL `https://github.com/Bungeetaco/home-assistant-nearby-flights`,
   category **Integration**, click Add.
3. Find "Nearby Flights" in the HACS integration list, open it, click
   **Download**, confirm.
4. **Restart Home Assistant** (required for HA to pick up the new
   `custom_components/nearby_flights/` folder).
5. **Settings → Devices & Services → Add Integration → search "Nearby
   Flights" → select it.**
6. Initial setup form asks for:
   - **Radius** — in **meters**. The default of `1000` (1km) is almost
     useless. *Tune this*: figure out the distance from this HA instance's
     home location (`Settings → System → General`, or read
     `hass.config.latitude`/`longitude`) to the nearest airport(s) of
     interest, and set radius generously past that — e.g. if the nearest
     major airport is ~35km away, don't set radius to exactly 35000; set it
     to something like `100000`-`225000` (100-225km) so approach/departure
     paths and nearby overflights are actually captured. A too-tight radius
     is the single most common cause of "the card shows no flights" even
     though the integration and card are both working correctly.
   - **Latitude / Longitude** — pre-filled from `hass.config`, almost always
     correct as-is.
   - **Scan interval** — in **seconds**. OpenSky's `states/all` bounding-box
     call is a single bulk request per poll (not one call per flight, unlike
     the original vendor's per-flight detail-fetch pattern), so there's no
     equivalent per-flight rate-limit risk. `60` is a reasonable default for
     a registered OpenSky account; go higher if you're watching your daily
     credit budget on a large radius.
   - **OpenSky client ID / secret** — **required**, from the OpenSky account
     API client mentioned in Prerequisites. Validated live against OpenSky's
     OAuth endpoint before the entry is created — a bad credential fails
     setup immediately with an error, not a silently-broken entity later.
7. After creation, open the integration's **Configure** (options flow) if you
   want to also set min/max altitude filters.
8. Confirm data is flowing: check the state of
   `sensor.nearby_flights_current_in_area` — it should have a non-empty
   `flights` attribute (a list of flight objects) within a minute or two of
   setup, and `stale: false`.

## Part 2 — Dashboard cards (manual install, no HACS)

1. Create the target directory inside the HA config folder:
   `<config>/www/community/nearby-flights-card/`
2. Copy these two files from this repo's `www/` folder into it:
   - `flight-panel-card.js`
   - `home-assistant-flightradar24-card-square.js`
   (`home-assistant-flightradar24-card.js` is an unpatched reference copy —
   not needed on the live install, skip it.)
3. Register both as Lovelace resources: **Settings → Dashboards → ⋮ (top
   right) → Resources → Add Resource**, type **JavaScript Module**, for each:
   - `/local/community/nearby-flights-card/flight-panel-card.js`
   - `/local/community/nearby-flights-card/home-assistant-flightradar24-card-square.js`

   (Files under `<config>/www/` are always served at `/local/...` by Home
   Assistant itself — this is independent of HACS, no `/hacsfiles/` prefix
   involved since this wasn't installed through HACS's own plugin mechanism.)
4. Hard-refresh the browser (or bump a `?v=` cache-busting query string on
   the resource URLs later if you ever update these files live) so the
   browser doesn't keep serving a cached copy. **If a dashboard is on a
   long-lived kiosk/wall-display browser tab**, a resource-URL cache-bust
   alone won't reach it — that tab needs an actual page reload (or full
   browser restart) after any card JS change, since it never re-navigates on
   its own.
5. Add the card to any dashboard view, e.g.:

   ```yaml
   type: custom:flight-panel-card
   entity: sensor.nearby_flights_current_in_area
   radius_km: 100   # MUST match (or be ≤) the integration's Radius/1000 from Part 1 step 6.
                     # Tune to the target home's actual distance from the
                     # nearest airport(s) — don't just copy 100 blindly.
   aspect_ratio: 2
   map_radar_size: 100
   ```

   Full list of optional config keys (units, ticker row limits, sort order,
   `backend_min_radius_km`, `backend_margin_factor`, `max_radius_km`,
   `sync_backend_radius`, etc.) is documented in the comment header at the
   top of `flight-panel-card.js` — read it before assuming a key doesn't
   exist.

## Verifying it worked

- `sensor.nearby_flights_current_in_area` has attribute `flights` as a
  non-empty list (once real air traffic is in range) and `stale: false`,
  with `status` (`Departing`/`Landing`/`Climbing`/`Descending`/`Cruising`/`On Ground`)
  and `~Xm to go`/`~Xh Ym to go` ETA (when a destination airport was resolved)
  populated per flight.
- The dashboard card renders a radar-style map plus a scrolling ticker list,
  not a red error box and not a "No flights currently in range" placeholder
  that never changes. (An occasional empty state is normal if there's
  genuinely nothing nearby right now — the radius tuning above is what
  prevents it being *permanently* empty.)
- Browser console (F12) has no `customElements` errors for
  `flight-panel-card` or `nearby-flights-map-card` — that specific error
  means one of the two resource URLs in Part 2 step 3 is wrong or missing.

## Known limitations / gotchas

- The backend integration directory (`custom_components/nearby_flights/`)
  will be silently overwritten if this same domain is ever updated through
  HACS from a *different* repository — don't add another repo claiming the
  `nearby_flights` domain as a HACS custom repository simultaneously.
- `radius` in the integration's config (meters) is a bounding-box
  half-width, not a true circle — real detected distances can exceed the
  configured radius by up to √2× near the box's corners. Not a bug, just how
  the bounding-box math works.
- adsbdb has no live schedule/timing data (only static route info), so
  `time_scheduled_departure`/`time_estimated_arrival`/etc. attributes are
  always `None`. The `status` field (derived from on-ground state + vertical
  rate) and a straight-line, current-position-based ETA (only when a
  destination airport was resolved) are the intended replacements for
  schedule-based phase/arrival display, and are what the ticker card
  actually renders now.
- The OpenSky area fetch has its own soft-block/failure caching with a grace
  window (same shape of protection the original vendor's patch had, just
  covering OpenSky failures instead) — see the comments in `api/flight.py`
  for the exact mechanism if debugging a real outage vs. a transient blip.
