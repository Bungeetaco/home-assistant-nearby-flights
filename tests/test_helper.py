"""Boundary tests for the pure helpers, flight_phase above all - its asymmetric
comparisons (>= +300 / <= -300 fpm, strict < 10,000 ft, <= 80 km) drive the
user-visible per-flight `status` and are exactly the kind of logic that breaks
silently on a refactor."""
import math

import pytest

from api.helper import (
    PHASE_LOW_ALTITUDE_FT,
    PHASE_NEAR_AIRPORT_KM,
    PHASE_VERTICAL_SPEED_THRESHOLD_FPM,
    bbox_from_point_radius,
    flight_phase,
    haversine_km,
    meters_to_feet,
    to_int,
)


class TestFlightPhase:
    def test_on_ground_wins_over_everything(self):
        assert flight_phase(True, 5000.0, 100.0, 1.0, 1.0) == "On Ground"

    def test_no_vertical_speed_means_unknown(self):
        assert flight_phase(False, None, 5000.0) is None

    @pytest.mark.parametrize("vs", [0.0, 299.9, -299.9])
    def test_below_threshold_is_cruising(self, vs):
        assert flight_phase(False, vs, 35000.0) == "Cruising"

    def test_climb_threshold_is_inclusive(self):
        assert flight_phase(False, PHASE_VERTICAL_SPEED_THRESHOLD_FPM, 35000.0) == "Climbing"

    def test_descent_threshold_is_inclusive(self):
        assert flight_phase(False, -PHASE_VERTICAL_SPEED_THRESHOLD_FPM, 35000.0) == "Descending"

    def test_low_altitude_boundary_is_strict(self):
        # 9999.9 ft is "low" (Departing); exactly 10,000 ft is not (Climbing).
        assert flight_phase(False, 1000.0, PHASE_LOW_ALTITUDE_FT - 0.1) == "Departing"
        assert flight_phase(False, 1000.0, PHASE_LOW_ALTITUDE_FT) == "Climbing"

    def test_near_airport_boundary_is_inclusive(self):
        # Climbing at high altitude: 80.0 km from origin => Departing; 80.1 => Climbing.
        assert (
            flight_phase(False, 1000.0, 20000.0, PHASE_NEAR_AIRPORT_KM, None) == "Departing"
        )
        assert (
            flight_phase(False, 1000.0, 20000.0, PHASE_NEAR_AIRPORT_KM + 0.1, None) == "Climbing"
        )

    def test_near_destination_promotes_descent_to_landing(self):
        assert flight_phase(False, -1000.0, 20000.0, None, 50.0) == "Landing"
        assert flight_phase(False, -1000.0, 20000.0, None, 200.0) == "Descending"

    def test_origin_distance_does_not_affect_descent(self):
        # Being near the ORIGIN must not turn a high-altitude descent into "Landing".
        assert flight_phase(False, -1000.0, 20000.0, 10.0, None) == "Descending"


class TestToInt:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(5, 5), ("5", 5), (True, 1), (None, None), ("x", None), ([], None), ({}, None)],
    )
    def test_total_over_arbitrary_input(self, value, expected):
        assert to_int(value) == expected


class TestGeo:
    def test_haversine_zero(self):
        assert haversine_km(45.0, -75.0, 45.0, -75.0) == 0.0

    def test_haversine_known_distance(self):
        # One degree of latitude is ~111 km anywhere on the globe.
        assert haversine_km(45.0, -75.0, 46.0, -75.0) == pytest.approx(111.2, abs=1.0)

    def test_bbox_is_centered_and_ordered(self):
        lamin, lomin, lamax, lomax = bbox_from_point_radius(45.0, -75.0, 100.0)
        assert lamin < 45.0 < lamax
        assert lomin < -75.0 < lomax
        # Longitude delta widens with latitude (1/cos scaling).
        assert (lomax - lomin) > (lamax - lamin)

    def test_meters_to_feet(self):
        assert meters_to_feet(None) is None
        assert meters_to_feet(304.8) == pytest.approx(1000.0)
        assert math.isclose(meters_to_feet(0.3048), 1.0)
