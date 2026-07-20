// flight-panel-card
//
// All user-facing behavior is driven by `this._config` (set via setConfig, i.e. editable
// in HA's dashboard card YAML/code editor) rather than baked into the JS. Every option
// below has a sensible default so a minimal config (just `entity`) still works.
//
// Supported config keys:
//   entity                 (required) the "flights in area" sensor
//                          (sensor.nearby_flights_current_in_area)
//   title                  card header text (default "✈️ Flights Overhead"; set to "" for none)
//   show_subtitle          show the live "N flights · closest Xkm · updated Zs ago" line
//                          under the title (default true)
//   scan_interval_seconds  the card's own polling cadence (default 30) — this is now the
//                          REAL polling interval, not an estimate. The card itself calls
//                          `homeassistant.update_entity` on this cadence, and only while
//                          it's actually mounted in the DOM AND the browser tab is visible
//                          (see connectedCallback/_startPolling) — pausing the instant the
//                          dashboard isn't being looked at. The backend integration's own
//                          scan_interval should be set to something very large (e.g. a day)
//                          as a dormant safety-net floor, since this card is what actually
//                          drives fetches now. This also means the OpenSky API only ever
//                          gets hit while someone is genuinely looking at the dashboard,
//                          not continuously in the background around the clock.
//   aspect_ratio            width/height ratio for the whole map+ticker row (default 2.5)
//   location_tracker       entity used as the map's center point (default "zone.home")
//   radius_km              map + ticker radius in km (default 75). The ticker only ever
//                          lists flights within this radius, filtered entirely client-side
//                          from whatever the backend integration already fetched — it used
//                          to show everything the backend had fetched regardless of this
//                          setting, which could list a flight with no visible marker on
//                          the map. Editable live from the card itself (see
//                          show_radius_input) — updates the map framing and ticker filter
//                          on every keystroke, no reload/network cost, since it's filtering
//                          data already sitting in the sensor's `flights` attribute. That
//                          live value is a per-browser localStorage override and takes
//                          priority over this config value once the user has typed into
//                          the radius box at least once.
//   show_radius_input      show an editable "Radius: __ km" box under the auto-refresh
//                          toggle so the radius can be changed without opening the card's
//                          YAML editor (default true)
//   max_radius_km          hard ceiling enforced on the radius input, both via the HTML
//                          field's max attribute and a JS clamp on every keystroke so it
//                          can't be typed past either (default 250).
//   backend_grow_debounce_ms  how long to wait after the last radius keystroke before
//                          even checking whether the backend needs to grow (default 3000).
//                          Deliberately generous — this is the only path in the card that
//                          can reach the backend's real (OpenSky) API, so it waits for
//                          typing to fully settle rather than firing mid-edit.
//   show_update_now_button  show an "Update Now" button under the radius input that forces
//                          an immediate backend poll via the core
//                          `homeassistant.update_entity` service, bypassing the scan
//                          interval (default true). Briefly disables itself after each
//                          click (see update_now_cooldown_ms) so it can't be spam-clicked
//                          into becoming its own rate-limit trigger.
//   update_now_cooldown_ms  minimum time the "Update Now" button stays disabled after a
//                          click, in milliseconds (default 5000).
//   sync_backend_radius    if the requested radius_km ever exceeds what the backend
//                          integration is currently fetching, grow the backend's own
//                          fetch radius (with margin, see backend_min_radius_km /
//                          backend_margin_factor) via its options flow so flights out
//                          that far actually exist in the data to filter (default true).
//                          This only ever GROWS the backend radius, never shrinks it and
//                          never fires for a radius already covered by the current
//                          backend fetch — typing/adjusting radius_km within the
//                          already-fetched envelope is 100% local and never touches the
//                          backend integration or reloads it. This design exists because
//                          reloading the backend integration on every radius tweak (the
//                          original implementation) triggers a fresh session with the
//                          backend's OpenSky API each time, and several reloads in quick
//                          succession got this integration rate-limited (HTTP 429) in
//                          practice — growing rarely, with margin, avoids that. Requires
//                          the logged-in HA user to be an admin, same as opening that
//                          integration's settings normally would.
//   backend_min_radius_km  the backend integration's fetch radius will never be grown to
//                          less than this floor (default 100) — keeps small radius_km
//                          values (e.g. 10-50km) from ever needing a backend grow at all.
//   backend_margin_factor  when a grow IS needed, the backend is set to
//                          radius_km * backend_margin_factor (default 1.5), not exactly
//                          radius_km, so nearby future increases also stay within the
//                          already-fetched envelope instead of needing another grow.
//   map_overscan           the map is framed slightly wider than radius_km (factor, default
//                          1.15 = +15%) so a flight right at the configured radius still has
//                          margin before the pane's edge, rather than being clipped by
//                          overflow:hidden. The ticker filter still uses the exact
//                          radius_km, not the overscanned value, so a flight can very
//                          occasionally appear as a marker without a ticker row (harmless)
//                          but never the reverse (a ticker row with no marker).
//   map_radar_size         map fill percentage of its square pane, 1-100 (default 100)
//   background_map         force a specific map style ("color"/"dark"/"voyager"/"bw"/...);
//                          omit to auto-pick "color"/"dark" from hass.themes.darkMode
//   background_map_opacity opacity override, used only when background_map is forced
//   background_map_opacity_light / _dark  opacity used per-mode in the auto (unforced) case
//   radar_grid_color       ring/cardinal-line color (default "transparent" i.e. hidden)
//   ring_distance          km between radar rings, cosmetic while rings are hidden (default 20)
//   aircraft_marker_size   "small"|"normal"|"large"|"x-large"|"xx-large" (default "x-large")
//   hide_range_label       hide the map's "Range: X km" overlay text (default true)
//   hide_map_list          hide the mounted card's own flight list, since the ticker
//                          replaces it (default true)
//   max_flights            cap passed to the mounted map card's own data handling (default 30)
//   max_ticker_rows        cap on how many rows the ticker shows, closest first (default
//                          12 -- only applied when this key is entirely unset; a config
//                          that explicitly sets it, to any value including a very large
//                          one, is always respected exactly as given and never silently
//                          reinterpreted as uncapped). 12 rows was picked as a reasonable
//                          fit for a ticker pane sharing space with the map on a 4K TV.
//   sort_by                flight field the ticker sorts by (default "distance")
//   sort_desc              sort descending instead of ascending (default false)
//   units                  { altitude: "ft"|"m", speed: "kt"|"kmh"|"mph", distance: "km"|"mi" }
//   show_ticker_icons       show the ⬆️➡️📍⏱🛬 glyphs in ticker rows (default true)
//   enable_map_links        make ticker rows clickable through to a live tracking page
//                           (ADS-B Exchange globe view, since the 2026-07-19 OpenSky
//                           backend switch) (default true)
//   show_autorefresh_toggle show/hide the auto-refresh switch in the header (default true)
//   default_auto_refresh    initial auto-refresh state before the user has ever touched the
//                          toggle on this device (default true; once touched, the choice
//                          persists in localStorage regardless of this default)
//   height                 (legacy) if set and aspect_ratio is not, height/1 is used as
//                          the ratio's denominator against a 1:1 assumption — prefer
//                          aspect_ratio directly.
//   backoff_enabled        while the backend's `stale` attribute is true (it's serving
//                          cached data because the OpenSky area feed came back empty or
//                          failed — see AREA_STALE_GRACE_S in custom_components/
//                          nearby_flights/api/flight.py), multiply the polling interval on every
//                          consecutive stale poll instead of continuing to hit the
//                          already-struggling endpoint at the normal cadence (default
//                          true). Resets to the base scan_interval_seconds the instant a
//                          poll comes back non-stale again.
//   backoff_factor          multiplier applied per consecutive stale poll, compounding
//                          (default 2 — 30s base becomes 60s, 120s, 240s, ...).
//   backoff_max_seconds    ceiling the backed-off interval will never exceed (default 300).
//   show_stale_indicator   append "· cached (feed may be blocked)" to the subtitle while
//                          the backend reports stale data (default true).
//   show_error_indicator   append "· update failed" to the subtitle, dot turned
//                          var(--error-color), once ERROR_STREAK_THRESHOLD (2)
//                          consecutive homeassistant.update_entity service calls fail
//                          outright -- e.g. a WebSocket drop or HA-side error, distinct
//                          from the backend merely reporting stale data above. Clears
//                          instantly on the next successful call. Takes visual
//                          precedence over paused/stale on the dot if more than one is
//                          true at once, since it's the most actionable state (default
//                          true).

// How many CONSECUTIVE homeassistant.update_entity service-call failures (from either
// the periodic background poll or the manual "Update Now" button) it takes before the
// error indicator lights up -- see show_error_indicator in the header comment block.
// A single transient blip (e.g. one dropped WebSocket frame) shouldn't flip a visible
// indicator on a TV dashboard nobody's actively debugging; requiring 2 in a row filters
// that out while still surfacing a genuinely broken connection quickly.
const ERROR_STREAK_THRESHOLD = 2;

// Ticker row cap applied only when max_ticker_rows is entirely unset in config (see the
// max_ticker_rows doc comment above) -- picked as a reasonable fit for a ticker pane
// that shares space with the map on a 4K TV dashboard section.
const DEFAULT_MAX_TICKER_ROWS = 12;

const UNIT_CONVERTERS = {
  altitude: {
    ft: { label: "ft", fromFt: (v) => v },
    m: { label: "m", fromFt: (v) => v * 0.3048 },
  },
  speed: {
    kt: { label: "kt", fromKt: (v) => v },
    kmh: { label: "km/h", fromKt: (v) => v * 1.852 },
    mph: { label: "mph", fromKt: (v) => v * 1.15078 },
  },
  distance: {
    km: { label: "km", fromKm: (v) => v },
    mi: { label: "mi", fromKm: (v) => v * 0.621371 },
  },
};

class FlightPanelCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) {
      throw new Error("flight-panel-card: 'entity' is required (the flights sensor)");
    }
    this._config = config;
    this._storageKey = `flight-panel-card-autorefresh-${config.entity}`;
    this._radiusStorageKey = `flight-panel-card-radius-${config.entity}`;
    const stored = localStorage.getItem(this._storageKey);
    const defaultAutoRefresh = config.default_auto_refresh ?? true;
    this._autoRefresh = stored === null ? defaultAutoRefresh : stored === "true";
    // Consecutive-stale-poll counter driving the backoff multiplier in
    // _effectivePollIntervalMs — reset whenever a poll comes back non-stale.
    this._staleStreak = 0;
    this._isStale = false;
    // Consecutive update_entity SERVICE CALL failure counter (distinct from staleness
    // above -- this tracks the call itself throwing, e.g. a WebSocket drop or HA-side
    // error, not the backend successfully answering with old data). Incremented in the
    // catch block of both _pollBackendNow and _handleUpdateNowClick, reset to 0 the
    // instant either succeeds. _hasError flips on once this crosses
    // ERROR_STREAK_THRESHOLD and drives the .fp-error subtitle state.
    this._errorStreak = 0;
    this._hasError = false;
    this._buildDom();
    this._mountMapChild();
    this._maybeGrowBackendRadius();
  }

  _buildDom() {
    // Fluid, aspect-ratio-driven sizing (same philosophy as the Windy iframe elsewhere
    // on this dashboard) instead of a fixed pixel height. A fixed height clipped the
    // bottom of the map whenever the card's actual rendered width changed (window
    // resize, sidebar toggle, breakpoint change) because the map pane's height no
    // longer matched what the map itself was drawn at. aspect-ratio recomputes on
    // every layout pass automatically, so it can't drift out of sync.
    const ratio = this._config.aspect_ratio || 2.5;
    const showToggle = this._config.show_autorefresh_toggle ?? true;
    const showSubtitle = this._config.show_subtitle ?? true;
    const showRadiusInput = this._config.show_radius_input ?? true;
    const showUpdateNow = this._config.show_update_now_button ?? true;
    const maxRadiusKm = this._config.max_radius_km ?? 250;
    const title = this._config.title === "" ? "" : this._config.title || "✈️ Flights Overhead";
    this.innerHTML = `
      <ha-card>
        <div class="fp-header">
          <div class="fp-heading-block">
            ${title ? `<div class="fp-title">${title}</div>` : ""}
            ${showSubtitle ? `<div class="fp-subtitle" id="fp-subtitle">Loading…</div>` : ""}
          </div>
          <div class="fp-header-controls">
            ${
              showToggle
                ? `<label class="fp-toggle">
                    <span class="fp-toggle-label">Auto-refresh</span>
                    <input type="checkbox" id="fp-autorefresh" ${this._autoRefresh ? "checked" : ""} />
                    <span class="fp-toggle-slider"></span>
                  </label>`
                : ""
            }
            ${
              showRadiusInput
                ? `<label class="fp-radius-control">
                    <span class="fp-toggle-label">Radius</span>
                    <input type="number" id="fp-radius-input" class="fp-radius-input" value="${this._radiusKm()}" min="1" max="${maxRadiusKm}" step="5" />
                    <span class="fp-toggle-label">km</span>
                  </label>`
                : ""
            }
            ${
              showUpdateNow
                ? `<button type="button" id="fp-update-now" class="fp-update-now-btn">Update Now</button>`
                : ""
            }
          </div>
        </div>
        <div class="fp-wrap" style="aspect-ratio:${ratio};">
          <div class="fp-map-pane" id="fp-map"></div>
          <div class="fp-ticker-pane" id="fp-ticker"></div>
        </div>
      </ha-card>
      <style>
        /* container-type here (not a @media viewport query) is deliberate: what actually
           determines whether the map+ticker split feels cramped is THIS CARD's own
           rendered width, not the browser window's -- the same card can sit in a wide
           4K section (fine, side-by-side) or a much narrower column/phone screen (ticker
           rows wrap badly once flight data is present, confirmed via screenshot testing
           at 390px/820px/3840px card widths) on the exact same dashboard. A container
           query reacts to the card's own box regardless of viewport size, so it stays
           correct if this card is ever placed in a narrower section too, which a plain
           @media (max-width) query could not do. */
        ha-card { overflow: hidden; container-type: inline-size; }
        .fp-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          padding: 12px 16px 0 16px;
          gap: 12px;
        }
        .fp-heading-block {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }
        .fp-title {
          font-size: 1.15em;
          font-weight: 600;
          letter-spacing: 0.01em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .fp-subtitle {
          font-size: 0.78em;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .fp-subtitle .fp-live-dot {
          display: inline-block;
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--success-color, #4caf50);
          margin-right: 5px;
          box-shadow: 0 0 4px var(--success-color, #4caf50);
        }
        .fp-subtitle.fp-paused .fp-live-dot {
          background: var(--warning-color, #ff9800);
          box-shadow: none;
          animation: none;
        }
        @keyframes fp-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.35; }
        }
        .fp-subtitle:not(.fp-paused) .fp-live-dot {
          animation: fp-pulse 2s ease-in-out infinite;
        }
        .fp-subtitle.fp-stale .fp-live-dot {
          background: #ffca28;
          box-shadow: 0 0 4px #ffca28;
        }
        .fp-subtitle.fp-error .fp-live-dot {
          background: var(--error-color, #db4437);
          box-shadow: 0 0 4px var(--error-color, #db4437);
        }
        .fp-header-controls {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 6px;
          flex-shrink: 0;
        }
        .fp-toggle {
          display: flex;
          align-items: center;
          gap: 6px;
          cursor: pointer;
          user-select: none;
        }
        .fp-toggle-label {
          font-size: 0.75em;
          color: var(--secondary-text-color);
        }
        .fp-radius-control {
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .fp-radius-input {
          width: 44px;
          font-size: 0.8em;
          text-align: right;
          border: 1px solid var(--divider-color, #444);
          border-radius: 4px;
          background: var(--card-background-color, transparent);
          color: var(--primary-text-color, inherit);
          padding: 2px 4px;
          font-family: inherit;
        }
        .fp-radius-input:focus {
          outline: none;
          border-color: var(--primary-color, #03a9f4);
        }
        /* Chrome/Safari number-input spinner arrows are cramped at this size and add
           little value here — hide them, the field is small and rarely needs fine
           +/-1 stepping via mouse. */
        .fp-radius-input::-webkit-outer-spin-button,
        .fp-radius-input::-webkit-inner-spin-button {
          -webkit-appearance: none;
          margin: 0;
        }
        .fp-update-now-btn {
          font-size: 0.72em;
          font-family: inherit;
          border: 1px solid var(--divider-color, #444);
          border-radius: 4px;
          background: var(--card-background-color, transparent);
          color: var(--primary-color, #03a9f4);
          padding: 3px 8px;
          cursor: pointer;
          transition: opacity 0.15s, background 0.15s;
        }
        .fp-update-now-btn:hover:not(:disabled) {
          background: var(--secondary-background-color, rgba(3, 169, 244, 0.08));
        }
        .fp-update-now-btn:disabled {
          opacity: 0.5;
          cursor: default;
          color: var(--secondary-text-color);
        }
        .fp-toggle input { display: none; }
        .fp-toggle-slider {
          position: relative;
          width: 34px;
          height: 18px;
          border-radius: 9px;
          background: var(--switch-unchecked-color, #939393);
          transition: background 0.2s;
          flex-shrink: 0;
        }
        .fp-toggle-slider::before {
          content: "";
          position: absolute;
          top: 2px;
          left: 2px;
          width: 14px;
          height: 14px;
          border-radius: 50%;
          background: white;
          transition: transform 0.2s;
        }
        .fp-toggle input:checked + .fp-toggle-slider {
          background: var(--switch-checked-color, var(--primary-color, #03a9f4));
        }
        .fp-toggle input:checked + .fp-toggle-slider::before {
          transform: translateX(16px);
        }
        .fp-wrap {
          display: flex;
          width: 100%;
          box-sizing: border-box;
          padding: 12px;
          gap: 12px;
        }
        .fp-map-pane {
          flex: 0 0 auto;
          height: 100%;
          aspect-ratio: 1 / 1;
          border-radius: 12px;
          overflow: hidden;
          position: relative;
        }
        /* The mounted child card (nearby-flights-map-card, a hand-patched fork of
           Springvar's flightradar24-card) brings its own <ha-card> chrome
           (background/shadow/border-radius) — strip that so it reads as part of THIS
           card rather than a nested card-in-a-card. The pane is always a perfect square
           matching the wrap's fluid height (see aspect-ratio above), and the child fills
           100% of it (map_radar_size: 100 by default) — no wasted space at any card width. */
        .fp-map-pane nearby-flights-map-card {
          display: block;
          height: 100%;
          --ha-card-background: transparent;
          --card-background-color: transparent;
          --ha-card-box-shadow: none;
          --ha-card-border-width: 0;
          --ha-card-border-radius: 12px;
        }
        .fp-ticker-pane {
          flex: 1 1 auto;
          min-width: 0;
          height: 100%;
          border-left: 1px solid var(--divider-color, #333);
          padding-left: 12px;
          overflow-y: auto;
          box-sizing: border-box;
        }
        /* Below this card width, the side-by-side split leaves the ticker pane too
           narrow for a real flight row (callsign + airline on one line, route/alt/speed
           icons on the next) -- confirmed via screenshot testing with real flight data
           at a 374px card width: "WJA608 · WestJet" wrapped across two lines and the
           route arrow got orphaned onto its own line. Stack map-on-top/ticker-below
           instead so both panes get the card's full width. aspect-ratio on .fp-wrap is
           set as an inline style from _buildDom() (this._config.aspect_ratio) so it wins
           over a plain class rule here -- !important is the correct, narrow tool for
           overriding a same-element inline style from a container query. */
        @container (max-width: 500px) {
          .fp-wrap {
            flex-direction: column;
            aspect-ratio: unset !important;
            height: auto;
          }
          .fp-map-pane {
            width: 100%;
            height: auto;
          }
          .fp-ticker-pane {
            height: auto;
            max-height: 340px;
            width: 100%;
            border-left: none;
            border-top: 1px solid var(--divider-color, #333);
            padding-left: 0;
            padding-top: 12px;
          }
        }
        .fp-row-link {
          display: block;
          text-decoration: none;
          color: inherit;
        }
        .fp-row {
          border: 1px solid var(--divider-color, #444);
          border-radius: 8px;
          padding: 6px 10px;
          margin-bottom: 8px;
          font-size: 0.82em;
          line-height: 1.35;
          transition: border-color 0.15s, background 0.15s;
        }
        .fp-row-link:hover .fp-row {
          border-color: var(--primary-color, #03a9f4);
          background: var(--secondary-background-color, rgba(3, 169, 244, 0.08));
        }
        .fp-row-link:last-child .fp-row,
        .fp-row:last-child { margin-bottom: 0; }
        .fp-row .fp-line1 {
          display: flex;
          justify-content: space-between;
          font-weight: 600;
        }
        .fp-row .fp-route { color: var(--secondary-text-color); font-weight: 400; }
        .fp-row .fp-callsign::after {
          content: "↗";
          display: inline-block;
          margin-left: 4px;
          opacity: 0;
          transform: translate(-2px, 1px);
          transition: opacity 0.15s, transform 0.15s;
        }
        .fp-row-link:hover .fp-callsign::after {
          opacity: 0.6;
          transform: translate(0, 1px);
        }
        .fp-row .fp-line2 {
          display: flex;
          flex-wrap: wrap;
          gap: 6px 10px;
          color: var(--secondary-text-color);
          margin-top: 3px;
        }
        .fp-empty {
          color: var(--secondary-text-color);
          font-size: 0.9em;
          padding: 8px 0;
        }
        .fp-ticker-footer {
          color: var(--secondary-text-color);
          font-size: 0.75em;
          text-align: center;
          padding-top: 8px;
          opacity: 0.8;
        }
      </style>
    `;
    this._mapPaneEl = this.querySelector("#fp-map");
    this._tickerEl = this.querySelector("#fp-ticker");
    this._subtitleEl = this.querySelector("#fp-subtitle");
    const toggleEl = this.querySelector("#fp-autorefresh");
    if (toggleEl) {
      toggleEl.addEventListener("change", (e) => {
        this._autoRefresh = e.target.checked;
        localStorage.setItem(this._storageKey, String(this._autoRefresh));
        if (this._autoRefresh && this._hass) {
          // Snap immediately to current data rather than waiting for the next state
          // change -- _applyHass only actually re-renders when _updateRequired is set
          // (see _subscribeToStateChanges), so force it here same as the first-ever
          // hass push does.
          this._updateRequired = true;
          this._applyHass(this._hass);
          this._maybeImmediatePoll();
        } else {
          this._updateSubtitle();
        }
        // _startPolling() self-guards on _autoRefresh, so calling it unconditionally
        // here both stops polling (unchecked) and (re)starts it (checked) correctly.
        this._startPolling();
      });
    }
    const radiusEl = this.querySelector("#fp-radius-input");
    if (radiusEl) {
      const maxRadiusKm = this._config.max_radius_km ?? 250;
      // Fires on every keystroke (per the "update live as I type" request) rather than
      // only on blur/Enter. This is safe to do per-keystroke because the local effects
      // (map reframe + ticker refilter) only touch data already sitting in memory — no
      // network call happens here. The one genuinely expensive step, potentially
      // growing the backend integration's own fetch radius, is separately debounced in
      // _onRadiusChanged so typing "500" doesn't attempt three backend reloads.
      radiusEl.addEventListener("input", () => {
        let n = Number(radiusEl.value);
        if (!Number.isFinite(n) || n <= 0) return; // mid-typing/empty state, wait for more input
        // The HTML `max` attribute alone doesn't stop a user from typing digits past it
        // (only the up/down spinner arrows respect it), so clamp explicitly here too.
        if (n > maxRadiusKm) {
          n = maxRadiusKm;
          radiusEl.value = n;
        }
        localStorage.setItem(this._radiusStorageKey, String(n));
        this._onRadiusChanged();
      });
      radiusEl.addEventListener("blur", () => {
        // On leaving the field, snap back to the last valid value if what's left behind
        // is invalid (e.g. the user cleared it entirely) — the "input" handler above
        // already ignored that state rather than acting on it, so nothing else needs
        // to happen for the valid case.
        const n = Number(radiusEl.value);
        if (!Number.isFinite(n) || n <= 0) radiusEl.value = this._radiusKm();
      });
      radiusEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter") radiusEl.blur();
      });
    }
    const updateNowEl = this.querySelector("#fp-update-now");
    if (updateNowEl) {
      updateNowEl.addEventListener("click", () => this._handleUpdateNowClick(updateNowEl));
    }
    // Ticks the "updated Xs ago" portion of the subtitle every
    // second, independent of how often hass actually pushes new data — otherwise those
    // only advance once per scan interval and look frozen in between. _buildDom() (and
    // so this interval) can run more than once over a single card instance's lifetime —
    // confirmed earlier this session that HA can call setConfig() again on an existing
    // element — so clear any previous interval first. Without this, each re-render
    // leaked an orphaned setInterval that ran forever in the background; on a kiosk
    // dashboard tab left open for hours/days across many card reconfigurations (which
    // happened a lot today, redeploying this file repeatedly), that leak compounds and
    // is exactly the kind of thing that makes a long-running browser tab feel
    // progressively more sluggish over time.
    clearInterval(this._subtitleTimer);
    if (this._subtitleEl) {
      this._subtitleTimer = setInterval(() => this._updateSubtitle(), 1000);
    }
  }

  // Called whenever the effective radius changes (the live radius box, on every
  // keystroke). Re-frames the mounted map and re-filters the ticker against the flights
  // already in hand — both are pure local operations against data already sitting in
  // memory, so they're safe and cheap to run on every keystroke. Only the backend-growth
  // check is deferred (debounced) below, since that's the one path that can reach out to
  // the backend's real (OpenSky) API.
  _onRadiusChanged() {
    if (this._mapCardEl && this._hass) {
      this._mapCardEl.setConfig(this._buildMapConfig(this._hass));
    }
    if (this._lastRawFlights) this._renderTicker(this._lastRawFlights);

    clearTimeout(this._backendGrowDebounceTimer);
    const debounceMs = this._config.backend_grow_debounce_ms ?? 3000;
    this._backendGrowDebounceTimer = setTimeout(() => this._maybeGrowBackendRadius(), debounceMs);
  }

  // Ensures the backend integration is fetching AT LEAST as far out as this card
  // currently wants to display, growing it (with margin) if not — never shrinking it,
  // and never touching it at all if the current fetch radius already covers the
  // requested radius_km. This deliberately does NOT keep the backend's radius equal to
  // the card's radius on every change (an earlier version did, and reloading the
  // backend integration on every tweak — which forces a fresh session with the
  // backend's OpenSky API each time — got this integration rate-limited (HTTP 429 in
  // the logs) after a burst of quick radius changes). Growing rarely, with margin,
  // means everyday radius adjustments (the live input box, config edits) stay 100%
  // local against already-fetched data and never risk that again.
  //
  // "Peeking" at the backend's current radius (creating an options flow and reading its
  // schema defaults) does NOT call OpenSky's API — it only reads this integration
  // entry's already-stored config — so that part is safe to do on every debounced
  // check. Only the actual submit step (when a grow is genuinely needed) reloads the
  // integration and touches the real API. The peeked-but-unsubmitted flow is explicitly
  // deleted afterward so it doesn't linger as orphaned flow state on the backend.
  async _maybeGrowBackendRadius() {
    if (this._config.sync_backend_radius === false) return;
    if (!this._hass || this._hass.user?.is_admin === false) return;
    const wantedKm = this._radiusKm();
    if (this._knownBackendRadiusKm != null && this._knownBackendRadiusKm >= wantedKm) return;

    let flowId;
    try {
      const entries = await this._hass.callWS({ type: "config_entries/get" });
      const entry = entries.find((e) => e.domain === "nearby_flights" && e.state === "loaded");
      if (!entry) return;

      const flow = await this._hass.callApi("POST", "config/config_entries/options/flow", {
        handler: entry.entry_id,
      });
      flowId = flow.flow_id;
      const schema = flow.data_schema || [];
      const currentValues = {};
      schema.forEach((field) => {
        if (field.default !== undefined) currentValues[field.name] = field.default;
        else if (field.description?.suggested_value !== undefined) {
          currentValues[field.name] = field.description.suggested_value;
        }
      });

      const currentRadiusKm = (currentValues.radius ?? 0) / 1000;
      this._knownBackendRadiusKm = currentRadiusKm;
      if (currentRadiusKm >= wantedKm) {
        // Someone else (or a previous session) already grew it far enough — nothing to
        // submit. Let the `finally` block below delete this peeked-but-unused flow.
        return;
      }

      const minKm = this._config.backend_min_radius_km ?? 100;
      const marginFactor = this._config.backend_margin_factor ?? 1.5;
      const targetKm = Math.max(minKm, wantedKm * marginFactor);
      currentValues.radius = targetKm * 1000;
      await this._hass.callApi("POST", `config/config_entries/options/flow/${flowId}`, currentValues);
      flowId = undefined; // submitted, not ours to delete anymore
      this._knownBackendRadiusKm = targetKm;
      console.info(
        `[flight-panel-card] grew backend fetch radius to ${targetKm} km ` +
          `to cover the requested ${wantedKm} km`
      );
    } catch (e) {
      console.warn("[flight-panel-card] backend radius grow check failed:", e);
    } finally {
      if (flowId) {
        // We peeked but never submitted (either an error before submit, or the
        // "already big enough" early-return) — clean up the abandoned flow.
        try {
          await this._hass.callApi("DELETE", `config/config_entries/options/flow/${flowId}`);
        } catch {
          /* best-effort cleanup, not worth surfacing a second error for */
        }
      }
    }
  }

  // Forces an immediate backend poll via HA's core `homeassistant.update_entity`
  // service, which for a CoordinatorEntity-backed sensor (confirmed this sensor is one
  // by reading custom_components/nearby_flights/sensor.py) triggers a real
  // coordinator.async_request_refresh() — an actual fresh fetch, not just re-reading
  // whatever's already cached. The button disables itself for a short cooldown
  // afterward so it can't be turned into its own rapid-fire rate-limit trigger by
  // repeated clicking, the same failure mode that caused the 429 a few rounds back.
  async _handleUpdateNowClick(buttonEl) {
    if (!this._hass) return;
    const cooldownMs = this._config.update_now_cooldown_ms ?? 5000;
    buttonEl.disabled = true;
    const originalLabel = buttonEl.textContent;
    buttonEl.textContent = "Updating…";
    try {
      await this._hass.callService("homeassistant", "update_entity", {
        entity_id: this._config.entity,
      });
      this._errorStreak = 0;
      if (this._hasError) {
        this._hasError = false;
        this._updateSubtitle();
      }
    } catch (e) {
      console.warn("[flight-panel-card] manual update_entity call failed:", e);
      this._errorStreak += 1;
      if (this._errorStreak >= ERROR_STREAK_THRESHOLD && !this._hasError) {
        this._hasError = true;
        this._updateSubtitle();
      }
    }
    setTimeout(() => {
      buttonEl.disabled = false;
      buttonEl.textContent = originalLabel;
    }, cooldownMs);
  }

  // Card-driven on-demand backend polling: the backend is only ever fetched while this
  // card is genuinely on-screen (attached to the DOM AND the browser tab is visible), not
  // continuously in the background 24/7 — there's no point paying OpenSky API calls (and
  // rate-limit risk) for a dashboard nobody is looking at. The backend integration's own
  // scan_interval is set to a large dormant value for exactly this reason; this card is
  // what actually drives the real cadence now, via forced homeassistant.update_entity
  // calls (confirmed to trigger a genuine coordinator refresh, not a cached re-read — see
  // _handleUpdateNowClick above).
  connectedCallback() {
    if (!this._visibilityHandler) {
      this._visibilityHandler = () => {
        if (document.hidden) {
          this._stopPolling();
        } else {
          this._maybeImmediatePoll();
          this._startPolling();
        }
      };
      document.addEventListener("visibilitychange", this._visibilityHandler);
    }
    this._maybeImmediatePoll();
    this._startPolling();
  }

  // Effective delay before the next poll: the configured base interval, multiplied by
  // backoff_factor once per consecutive stale poll (compounding), capped at
  // backoff_max_seconds. See the backoff_* doc comments at the top of this file — even
  // though OpenSky publishes its rate limits (unlike the old backend's undocumented
  // one), a large radius or a burst of dashboard reloads can still eat through the
  // daily credit budget faster than expected, so back off automatically whenever the
  // backend itself reports it's serving cached data (self.area_stale in the Python
  // coordinator) rather than continuing to hit an already-struggling endpoint at the
  // normal cadence.
  _effectivePollIntervalMs() {
    const baseMs = (this._config.scan_interval_seconds ?? 30) * 1000;
    if (!(this._config.backoff_enabled ?? true) || this._staleStreak <= 0) return baseMs;
    const factor = this._config.backoff_factor ?? 2;
    const maxMs = (this._config.backoff_max_seconds ?? 300) * 1000;
    return Math.min(baseMs * Math.pow(factor, this._staleStreak), maxMs);
  }

  // (Re)schedules the next poll. Self-guards on every relevant condition, so every call
  // site can just call this unconditionally rather than duplicating the "should we
  // actually be polling right now" logic — always clears any existing timer first, so
  // it's also safe to call repeatedly (e.g. from the auto-refresh toggle). Uses a
  // self-rescheduling setTimeout rather than setInterval specifically so each cycle can
  // pick a fresh delay from _effectivePollIntervalMs() — a fixed setInterval can't adapt
  // once _staleStreak changes mid-run.
  _startPolling() {
    this._stopPolling();
    if (!this._autoRefresh || !this.isConnected || document.hidden || !this._hass) return;
    this._pollTimer = setTimeout(async () => {
      await this._pollBackendNow();
      this._startPolling();
    }, this._effectivePollIntervalMs());
  }

  _stopPolling() {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
  }

  // Fired once whenever the card newly becomes visible (mount, tab refocus, hass first
  // arriving, auto-refresh re-enabled) — forces an immediate fetch UNLESS the entity's
  // last update is already fresher than one scan interval, so repeatedly switching
  // dashboard views or alt-tabbing back and forth doesn't force a redundant fetch every
  // single time.
  _maybeImmediatePoll() {
    if (!this._hass) return;
    const stateObj = this._hass.states[this._config.entity];
    const scanIntervalSec = this._config.scan_interval_seconds ?? 30;
    if (stateObj?.last_updated) {
      const ageSec = (Date.now() - new Date(stateObj.last_updated).getTime()) / 1000;
      if (ageSec < scanIntervalSec) return;
    }
    this._pollBackendNow();
  }

  async _pollBackendNow() {
    if (!this._hass || this._pollInFlight) return;
    this._pollInFlight = true;
    try {
      await this._hass.callService("homeassistant", "update_entity", {
        entity_id: this._config.entity,
      });
      this._errorStreak = 0;
      if (this._hasError) {
        this._hasError = false;
        this._updateSubtitle();
      }
    } catch (e) {
      console.warn("[flight-panel-card] background poll failed:", e);
      this._errorStreak += 1;
      if (this._errorStreak >= ERROR_STREAK_THRESHOLD && !this._hasError) {
        this._hasError = true;
        this._updateSubtitle();
      }
    } finally {
      this._pollInFlight = false;
    }
  }

  disconnectedCallback() {
    if (this._subtitleTimer) clearInterval(this._subtitleTimer);
    clearTimeout(this._backendGrowDebounceTimer);
    this._stopPolling();
    if (this._visibilityHandler) {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
      this._visibilityHandler = null;
    }
    if (this._unsubStateChangesPromise) {
      this._unsubStateChangesPromise.then((unsub) => unsub());
      this._unsubStateChangesPromise = null;
    }
  }

  // Picking the map's light/dark tile style: the mounted map card's own
  // background_map: "system" option guesses via the browser's prefers-color-scheme
  // media query, which doesn't necessarily match HA's actual selected theme (a user can
  // run HA in light mode on a dark-OS device or vice versa) — that mismatch, plus the
  // dark tile style being too high-contrast at high opacity, is what was "blinding" in
  // dark mode. hass.themes.darkMode is HA's own authoritative light/dark flag, and each
  // mode gets its own tuned opacity instead of one-size-fits-all. A user can still force
  // a single style via config.background_map, bypassing all of this.
  _mapStyleFor(hass) {
    const darkMode = hass?.themes?.darkMode ?? false;
    if (this._config.background_map) {
      return { style: this._config.background_map, opacity: this._config.background_map_opacity ?? 0.9, darkMode };
    }
    return darkMode
      ? { style: "dark", opacity: this._config.background_map_opacity_dark ?? 0.55, darkMode }
      : { style: "color", opacity: this._config.background_map_opacity_light ?? 0.9, darkMode };
  }

  _radiusKm() {
    const maxRadiusKm = this._config.max_radius_km ?? 250;
    const override = localStorage.getItem(this._radiusStorageKey);
    if (override !== null) {
      const n = Number(override);
      // Clamped here too (not just in the input's keystroke handler) so a value saved
      // to localStorage before max_radius_km was lowered — or before this cap existed
      // at all — can never silently exceed the current ceiling.
      if (Number.isFinite(n) && n > 0) return Math.min(n, maxRadiusKm);
    }
    return Math.min(this._config.radius_km || 75, maxRadiusKm);
  }

  _buildMapConfig(hass) {
    const radiusKm = this._radiusKm();
    const overscan = this._config.map_overscan ?? 1.15;
    const mapRangeKm = radiusKm * overscan;
    const { style, opacity } = this._mapStyleFor(hass);
    const units = this._config.units || {};
    return {
      type: "custom:nearby-flights-map-card",
      location_tracker: this._config.location_tracker || "zone.home",
      flights_entity: this._config.entity,
      units: {
        altitude: units.altitude || "ft",
        speed: units.speed === "kmh" || units.speed === "mph" ? units.speed : "kts",
        distance: units.distance === "mi" ? "miles" : "km",
      },
      max_flights: this._config.max_flights ?? 30,
      radar: {
        // The map is framed slightly wider than the ticker's actual filter radius (see
        // map_overscan doc comment above) so a flight right at the configured radius has
        // margin before the pane's edge instead of its (fixed CSS pixel size) marker
        // icon getting clipped by the pane's overflow:hidden — this got worse the
        // smaller the fluid-sized map rendered, since the marker's fixed footprint is a
        // bigger fraction of a smaller map.
        range: mapRangeKm,
        max_range: mapRangeKm,
        ring_distance: this._config.ring_distance ?? 20,
        background_map: style,
        background_map_opacity: opacity,
        radar_size: this._config.map_radar_size ?? 100,
        "radar-grid-color": this._config.radar_grid_color || "transparent",
        "aircraft-marker-size": this._config.aircraft_marker_size || "x-large",
        hide_range: this._config.hide_range_label ?? true,
      },
      list: { hide: this._config.hide_map_list ?? true },
    };
  }

  async _mountMapChild() {
    if (!window.loadCardHelpers) {
      // Extremely old HA frontend without the helpers API — skip the map, ticker
      // still works standalone.
      return;
    }
    const helpers = await window.loadCardHelpers();
    this._lastDarkMode = this._hass?.themes?.darkMode ?? false;
    const mapConfig = this._buildMapConfig(this._hass);
    const mapEl = helpers.createCardElement(mapConfig);
    mapEl.hass = this._hass;
    this._mapPaneEl.innerHTML = "";
    this._mapPaneEl.appendChild(mapEl);
    this._mapCardEl = mapEl;
  }

  set hass(hass) {
    // setConfig() (where _maybeGrowBackendRadius is first called) runs before HA ever
    // hands the card a hass object, so that first attempt always no-ops on the "!this._hass"
    // guard — retry here the moment hass genuinely becomes available for the first time.
    const firstHass = !this._hass;
    this._hass = hass;
    // Same pattern the mounted map card itself uses for this
    // exact problem: hass is a single shared object pushed to every card on the
    // dashboard whenever *any* entity anywhere changes, not just this card's own
    // flight sensor. Subscribing directly to state_changed, filtered to our entity,
    // means _updateRequired only ever goes true for a genuine change to THIS entity --
    // no reactive timestamp-comparison needed, and _applyHass below is cheap to call on
    // every hass push since it no-ops immediately unless a real change was signaled.
    // An earlier version of this file compared stateObj.last_updated on every push
    // instead; this is strictly better, since the noise never reaches this card's own
    // code at all rather than being filtered out after the fact.
    this._unsubStateChangesPromise ||= this._subscribeToStateChanges(hass);
    if (firstHass) this._updateRequired = true; // render once immediately with whatever's already there
    if (this._autoRefresh) this._applyHass(hass);
    if (firstHass) {
      this._maybeGrowBackendRadius();
      // connectedCallback may have already run and no-opped (no hass yet at that point) —
      // catch up here the moment hass genuinely arrives, same reasoning as the backend
      // radius grow retry just above.
      this._maybeImmediatePoll();
      this._startPolling();
    }
  }

  // Subscribes directly to HA's state_changed event bus, filtered to this card's own
  // flight entity -- the same mechanism the mounted map card uses. Returns a promise
  // resolving to an unsubscribe function (HA's own WS API
  // convention); disconnectedCallback awaits and calls it.
  async _subscribeToStateChanges(hass) {
    try {
      return await hass.connection.subscribeEvents((ev) => {
        if (ev.data.entity_id === this._config.entity) this._updateRequired = true;
      }, "state_changed");
    } catch (e) {
      console.warn("[flight-panel-card] _subscribeToStateChanges failed:", e);
      return () => {};
    }
  }

  _applyHass(hass) {
    if (this._mapCardEl) {
      const darkMode = hass?.themes?.darkMode ?? false;
      if (!this._config.background_map && darkMode !== this._lastDarkMode) {
        this._lastDarkMode = darkMode;
        this._mapCardEl.setConfig(this._buildMapConfig(hass));
      }
      this._mapCardEl.hass = hass;
    }
    if (!this._updateRequired) return;
    const stateObj = hass.states[this._config.entity];
    if (!stateObj) return;
    this._updateRequired = false;
    // The entity's own server-side timestamp, not Date.now() -- this is what actually
    // drives the "updated Xs ago" display, and using the real backend time (rather than
    // "whenever my browser happened to notice") keeps it accurate across reconnects,
    // slow hass propagation, or a freshly (re)mounted card instance, none of which mean
    // the underlying data is actually brand new.
    this._lastUpdateTs = new Date(stateObj.last_updated).getTime();
    const flights = stateObj.attributes.flights || [];
    this._lastRawFlights = flights;
    // The backend (custom_components/nearby_flights/api/flight.py, AREA_STALE_GRACE_S)
    // sets this true when the OpenSky area feed came back empty/failed and it's serving
    // the last known-good list instead of wiping it -- i.e. flights here may be real but old.
    // Track how many CONSECUTIVE pushes have been stale so _effectivePollIntervalMs can
    // back off progressively rather than jumping straight to the max on the first one.
    const wasStale = this._isStale;
    this._isStale = !!stateObj.attributes.stale;
    this._staleStreak = this._isStale ? this._staleStreak + 1 : 0;
    this._renderTicker(flights);
    // Reschedule with the freshly recomputed backoff interval whenever staleness just
    // changed (entering backoff, or recovering from it) -- otherwise the currently
    // pending timer keeps running at whatever interval was in effect when it was last
    // scheduled, so backing off (or recovering) wouldn't take effect until the cycle
    // after next. _startPolling() no-ops safely if we're not actually supposed to be
    // polling right now (autoRefresh off, tab hidden, etc).
    if (this._isStale !== wasStale) this._startPolling();
  }

  _fmtDuration(seconds) {
    if (seconds == null || isNaN(seconds)) return null;
    const sign = seconds < 0 ? "-" : "";
    seconds = Math.abs(Math.round(seconds));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${sign}${h}h ${m}m`;
  }

  // Great-circle distance in km (same haversine formula the backend uses in
  // api/helper.py) - kept as a small local copy since this needs to run client-side
  // against live position + destination-airport coordinates already sitting in the
  // flight dict, not something worth a round trip to ask the backend for.
  _haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  // Straight-line ("as the crow flies") ETA to the destination airport, from current
  // position + current ground speed. adsbdb has no live schedule/estimated-arrival
  // data (see the dep/arr comment in _renderTicker below), so this is the only ETA
  // source available - only computed when the destination airport's coordinates are
  // actually known (adsbdb resolved a route for this callsign) and the flight is
  // airborne with a sane ground speed, never guessed from an unknown destination.
  // Deliberately approximate, not authoritative: it's great-circle distance divided by
  // *current* ground speed, so it ignores remaining path curvature (departure/arrival
  // procedures rarely fly a straight line to/from the airport) and won't reflect the
  // final descent's typically-lower speed until the aircraft has actually slowed down -
  // good enough for "roughly how long", not a real arrival-time guarantee.
  _estimateEtaMinutes(f) {
    const MIN_ETA_SPEED_KT = 30; // below this, ground speed is too near-zero/noisy for a sane estimate
    if (
      f.on_ground ||
      f.airport_destination_latitude == null ||
      f.airport_destination_longitude == null ||
      f.latitude == null ||
      f.longitude == null ||
      f.ground_speed == null ||
      f.ground_speed < MIN_ETA_SPEED_KT
    ) {
      return null;
    }
    const distKm = this._haversineKm(
      f.latitude, f.longitude, f.airport_destination_latitude, f.airport_destination_longitude
    );
    const speedKmh = f.ground_speed * 1.852; // knots -> km/h
    return (distKm / speedKmh) * 60;
  }

  _tickerUnits() {
    const units = this._config.units || {};
    const altKey = UNIT_CONVERTERS.altitude[units.altitude] ? units.altitude : "ft";
    const spdKey = UNIT_CONVERTERS.speed[units.speed] ? units.speed : "kt";
    const distKey = UNIT_CONVERTERS.distance[units.distance] ? units.distance : "km";
    return {
      altitude: UNIT_CONVERTERS.altitude[altKey],
      speed: UNIT_CONVERTERS.speed[spdKey],
      distance: UNIT_CONVERTERS.distance[distKey],
    };
  }

  _renderTicker(flights) {
    // Only ever list flights the map itself would also plot within its configured
    // radius. The sensor's raw `flights` attribute reflects the *backend* integration's
    // own (often larger) fetch radius — without this filter, tightening radius_km on
    // just this card left the ticker still showing flights out at the backend's full
    // radius, each with no corresponding marker anywhere on the (smaller) map.
    const radiusKm = this._radiusKm();
    const inRange = flights.filter((f) => f.distance == null || f.distance <= radiusKm);

    // NOTE: _lastUpdateTs is NOT touched here. This function also runs from
    // _onRadiusChanged() to re-filter already-fetched data against a new radius --
    // no new backend fetch happened in that case, so stamping "just now" here would
    // be wrong (this was a real bug: adjusting the radius input used to falsely reset
    // the "updated Xs ago" display). _lastUpdateTs is set exactly once, in _applyHass,
    // from the entity's own last_updated -- the only place a genuine new fetch is
    // actually observed.
    this._lastFlightCount = inRange.length;
    const closestKm = inRange.reduce((min, f) => (f.distance != null && f.distance < min ? f.distance : min), Infinity);
    this._lastClosestDisplay = Number.isFinite(closestKm)
      ? (() => {
          const u = this._tickerUnits();
          return `${Math.round(u.distance.fromKm(closestKm))} ${u.distance.label}`;
        })()
      : null;
    this._updateSubtitle();

    if (!inRange.length) {
      this._tickerEl.innerHTML = `<div class="fp-empty">No flights currently in range.</div>`;
      return;
    }
    const now = Date.now() / 1000;
    const sortBy = this._config.sort_by || "distance";
    const sortDesc = this._config.sort_desc ?? false;
    const sorted = [...inRange].sort((a, b) => {
      const av = a[sortBy] ?? 1e12;
      const bv = b[sortBy] ?? 1e12;
      return sortDesc ? bv - av : av - bv;
    });
    // `??` (not `||`) deliberately distinguishes "key entirely absent from config"
    // (undefined -> falls through to DEFAULT_MAX_TICKER_ROWS) from "key explicitly set,
    // including to a falsy value like 0" (any explicit value, 0 included, is respected
    // exactly as configured -- 0 preserves the pre-existing "falsy means unlimited"
    // behavior a couple lines below, unchanged from before this default was introduced).
    // Only the previously-undocumented "no cap at all" behavior for configs that never
    // mention max_ticker_rows changes here; anything that already sets the key keeps
    // behaving exactly as it did.
    const maxRows = this._config.max_ticker_rows ?? DEFAULT_MAX_TICKER_ROWS;
    const limited = maxRows ? sorted.slice(0, maxRows) : sorted;
    const showIcons = this._config.show_ticker_icons ?? true;
    const enableLinks = this._config.enable_map_links ?? true;
    const u = this._tickerUnits();

    const rowsHtml = limited
      .map((f) => {
        const callsign = f.callsign || f.flight_number || "Unknown";
        const airline = f.airline_short || "";
        const origin = f.airport_origin_code_iata || "?";
        const dest = f.airport_destination_code_iata || "?";
        const alt =
          f.altitude != null
            ? `${Math.round(u.altitude.fromFt(f.altitude)).toLocaleString()} ${u.altitude.label}`
            : "—";
        const spd = f.ground_speed != null ? `${Math.round(u.speed.fromKt(f.ground_speed))} ${u.speed.label}` : "—";
        const dist = f.distance != null ? `${Math.round(u.distance.fromKm(f.distance))} ${u.distance.label}` : "—";

        const dep = f.time_real_departure || f.time_estimated_departure || f.time_scheduled_departure;
        const flownFor = dep ? this._fmtDuration(now - dep) : null;

        const arr = f.time_estimated_arrival || f.time_scheduled_arrival;
        let eta = null;
        if (arr) {
          const diff = arr - now;
          eta = diff <= 0 ? "Landing" : `${this._fmtDuration(diff)} to go`;
        }

        // adsbdb (the backend's route-enrichment source) has no live timing data, so
        // dep/arr above are always null and the flownFor span below never renders. The
        // `!dep` guard on it is currently always true but kept explicit rather than
        // assumed, in case a future backend ever supplies real schedule times again.
        // eta (schedule-based) is similarly always null - positionEta (great-circle
        // distance-to-destination / current ground speed, see _estimateEtaMinutes
        // above) is what actually renders in that slot today, but eta still takes
        // priority if a future backend ever provides it, since real data beats an
        // estimate.
        const etaMinutes = this._estimateEtaMinutes(f);
        const positionEta =
          etaMinutes == null ? null : etaMinutes < 1 ? "Landing" : `~${Math.round(etaMinutes)}m to go`;
        const displayEta = eta || positionEta;

        const phaseIcons = { Departing: "🛫", Landing: "🛬", "On Ground": "🛬" };
        const phase = !dep && !arr && f.status && f.status !== "Cruising" ? f.status : null;

        const rowInner = `
            <div class="fp-line1">
              <span class="fp-callsign">${callsign}${airline ? ` · ${airline}` : ""}</span>
              <span class="fp-route">${origin} → ${dest}</span>
            </div>
            <div class="fp-line2">
              <span>${showIcons ? "⬆️ " : ""}${alt}</span>
              <span>${showIcons ? "➡️ " : ""}${spd}</span>
              <span>${showIcons ? "📍 " : ""}${dist}</span>
              ${flownFor ? `<span>${showIcons ? "⏱ " : ""}${flownFor} flown</span>` : ""}
              ${displayEta ? `<span>${showIcons ? "🛬 " : ""}${displayEta}</span>` : ""}
              ${phase ? `<span>${showIcons ? (phaseIcons[phase] || "") + " " : ""}${phase}</span>` : ""}
            </div>
        `;

        // Backend switched to OpenSky+adsbdb (2026-07-19) — f.id is now the aircraft's
        // icao24 hex (a stable per-airframe address), not the old backend's own
        // rotating per-flight id, so its old {slug}/{id}-style web permalink format no
        // longer resolves to anything real. ADS-B Exchange's globe view takes an
        // icao24 directly and needs no callsign/slug, so it's a straightforward swap
        // rather than a lost feature.
        const icao24 = f.aircraft_icao_24bit || f.id;
        if (enableLinks && icao24) {
          const url = `https://globe.adsbexchange.com/?icao=${encodeURIComponent(icao24)}`;
          return `<a class="fp-row-link" href="${url}" target="_blank" rel="noopener noreferrer">
            <div class="fp-row">${rowInner}</div>
          </a>`;
        }
        return `<div class="fp-row">${rowInner}</div>`;
      })
      .join("");

    const footerHtml =
      maxRows && sorted.length > maxRows
        ? `<div class="fp-ticker-footer">Showing ${maxRows} of ${sorted.length} flights</div>`
        : "";
    this._tickerEl.innerHTML = rowsHtml + footerHtml;
  }

  _fmtAge(seconds) {
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h`;
  }

  _updateSubtitle() {
    if (!this._subtitleEl) return;
    const paused = !this._autoRefresh;
    // Stale (backend soft-block, serving cached data) gets its own visual treatment,
    // distinct from paused (which is a deliberate user choice) -- both dim/color the dot,
    // but a real reader needs to tell "I turned this off" apart from "the backend is
    // giving us old data right now and there's nothing to click to fix it."
    const stale = this._isStale;
    // Error (the update_entity SERVICE CALL itself failing outright -- WebSocket drop,
    // HA-side error) is a distinct, more urgent failure mode than stale (the backend
    // successfully answered, just with old/cached data) or paused (a deliberate user
    // choice). Only one dot color can be shown at a time, so error wins that slot when
    // more than one is true at once -- "something is actually broken" is more
    // actionable than "auto-refresh is off" or "backend served cached data". The text
    // suffixes below are NOT mutually exclusive the same way -- each one that applies
    // is appended, so no information is lost, it's just de-prioritized in the dot.
    const error = this._hasError;
    this._subtitleEl.classList.toggle("fp-error", error);
    this._subtitleEl.classList.toggle("fp-paused", paused && !error);
    this._subtitleEl.classList.toggle("fp-stale", stale && !paused && !error);
    const count = this._lastFlightCount;
    const countText = count == null ? "Loading…" : count === 1 ? "1 flight" : `${count} flights`;
    const closestText = this._lastClosestDisplay ? ` · closest ${this._lastClosestDisplay}` : "";

    let ageText = "";
    if (this._lastUpdateTs != null) {
      const secs = Math.round((Date.now() - this._lastUpdateTs) / 1000);
      ageText = secs < 5 ? " · just now" : ` · updated ${this._fmtAge(secs)} ago`;
    }
    const showStaleIndicator = this._config.show_stale_indicator ?? true;
    const staleText = stale && showStaleIndicator ? " · cached (feed may be blocked)" : "";
    const showErrorIndicator = this._config.show_error_indicator ?? true;
    const errorText = error && showErrorIndicator ? " · update failed" : "";
    const statusText = paused ? " · paused" : "";
    this._subtitleEl.innerHTML = `<span class="fp-live-dot"></span>${countText}${closestText}${ageText}${staleText}${errorText}${statusText}`;
  }

  getCardSize() {
    return 6;
  }
}

customElements.define("flight-panel-card", FlightPanelCard);
