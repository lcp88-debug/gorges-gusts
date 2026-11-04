"""All routes, riders and wind here are made up."""

import math
import random

import pytest

from app.wind import (
    RiderProfile,
    Step,
    Wind,
    build_steps,
    decode_polyline,
    effort_power,
    wind_effect,
)

FULL_WIND = lambda step, wind_from: 1.0  # noqa: E731
RIDER = RiderProfile()
METERS_PER_DEGREE_LAT = 111_195


def road(length_m: float, bearing: float, grade: float = 0.0) -> list[Step]:
    """A straight road split into 25 m steps."""
    count = round(length_m / 25)
    return [Step(45.0, -120.0, length_m / count, bearing, grade) for _ in range(count)]


def line_north(length_m: float) -> list[tuple[float, float]]:
    return [(45.0, -120.0), (45.0 + length_m / METERS_PER_DEGREE_LAT, -120.0)]


def angle_difference(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


class TestDecodePolyline:
    def test_decodes_the_reference_example(self):
        assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
            (38.5, -120.2),
            (40.7, -120.95),
            (43.252, -126.453),
        ]

    def test_empty_route_has_no_points(self):
        assert decode_polyline("") == []

    def test_rejects_a_cut_off_polyline(self):
        with pytest.raises(ValueError):
            decode_polyline("_p~iF")


class TestBuildSteps:
    def test_steps_add_up_to_the_route_length(self):
        steps = build_steps(line_north(1000), 0.0)
        assert len(steps) == 40
        assert sum(step.length_m for step in steps) == pytest.approx(1000, rel=1e-3)

    def test_very_short_route_still_gets_one_step(self):
        assert len(build_steps(line_north(5), 0.0)) == 1

    @pytest.mark.parametrize(
        "end, bearing",
        [((45.01, -120.0), 0), ((45.0, -119.99), 90), ((44.99, -120.0), 180), ((45.0, -120.01), 270)],
    )
    def test_bearing_follows_the_direction_of_travel(self, end, bearing):
        steps = build_steps([(45.0, -120.0), end], 0.0)
        assert all(angle_difference(step.bearing, bearing) < 0.1 for step in steps)

    def test_bearing_changes_around_a_bend(self):
        corner = 45.0 + 500 / METERS_PER_DEGREE_LAT
        east = -120.0 + 500 / (METERS_PER_DEGREE_LAT * math.cos(math.radians(corner)))
        steps = build_steps([(45.0, -120.0), (corner, -120.0), (corner, east)], 0.0)
        assert angle_difference(steps[0].bearing, 0) < 1
        assert angle_difference(steps[-1].bearing, 90) < 1

    def test_repeated_points_are_ignored(self):
        start, end = line_north(500)
        steps = build_steps([start, start, end, end], 0.0)
        assert all(angle_difference(step.bearing, 0) < 0.1 for step in steps)

    def test_rejects_a_route_with_no_length(self):
        with pytest.raises(ValueError):
            build_steps([(45.0, -120.0), (45.0, -120.0)], 0.0)

    def test_without_a_map_every_step_uses_the_average_grade(self):
        assert {step.grade for step in build_steps(line_north(500), 0.05)} == {0.05}

    def test_map_gradient_is_shifted_to_match_the_average_grade(self):
        start = 45.0
        elevation = lambda lat, lng: (lat - start) * METERS_PER_DEGREE_LAT * 0.03  # noqa: E731
        steps = build_steps(line_north(2000), 0.05, elevation)
        assert sum(step.grade for step in steps) / len(steps) == pytest.approx(0.05)

    def test_gaps_in_the_map_use_the_average_grade(self):
        steps = build_steps(line_north(500), 0.05, lambda lat, lng: None)
        assert {step.grade for step in steps} == {0.05}

    def test_smoothing_keeps_direction_accurate_on_a_wobbly_route(self):
        random.seed(1)
        wobble = 3 / METERS_PER_DEGREE_LAT
        points = [
            (45.0 + index * 10 / METERS_PER_DEGREE_LAT, -120.0 + random.uniform(-wobble, wobble))
            for index in range(200)
        ]
        steps = build_steps(points, 0.0)
        errors = [angle_difference(step.bearing, 0) for step in steps]
        assert sum(errors) / len(errors) < 6


class TestWindEffect:
    def test_no_wind_means_no_change(self):
        effect = wind_effect(road(2000, 0), RIDER, Wind(0, 0), FULL_WIND)
        assert effect.percent == pytest.approx(0)

    def test_headwind_is_slower_and_tailwind_is_faster(self):
        steps = road(2000, 0)
        assert wind_effect(steps, RIDER, Wind(5, 0), FULL_WIND).percent > 0
        assert wind_effect(steps, RIDER, Wind(5, 180), FULL_WIND).percent < 0

    def test_headwind_costs_more_than_the_same_tailwind_saves(self):
        steps = road(2000, 0)
        head = wind_effect(steps, RIDER, Wind(5, 0), FULL_WIND).percent
        tail = wind_effect(steps, RIDER, Wind(5, 180), FULL_WIND).percent
        assert head > -tail

    def test_pure_crosswind_adds_only_a_little_drag(self):
        steps = road(2000, 0)
        cross = wind_effect(steps, RIDER, Wind(5, 90), FULL_WIND).percent
        head = wind_effect(steps, RIDER, Wind(5, 0), FULL_WIND).percent
        assert 0 < cross < head / 2

    def test_crosswind_from_either_side_is_the_same(self):
        steps = road(2000, 0)
        left = wind_effect(steps, RIDER, Wind(5, 270), FULL_WIND).percent
        right = wind_effect(steps, RIDER, Wind(5, 90), FULL_WIND).percent
        assert left == pytest.approx(right)

    def test_wind_matters_less_on_a_climb(self):
        flat = wind_effect(road(2000, 0), RIDER, Wind(5, 0), FULL_WIND).percent
        climb = wind_effect(road(2000, 0, 0.07), RIDER, Wind(5, 0), FULL_WIND).percent
        assert 0 < climb < flat

    def test_a_there_and_back_route_is_slower_overall(self):
        steps = road(1000, 0) + road(1000, 180)
        assert wind_effect(steps, RIDER, Wind(5, 0), FULL_WIND).percent > 0

    def test_shelter_reduces_the_effect(self):
        steps = road(2000, 0)
        open_road = wind_effect(steps, RIDER, Wind(5, 0), FULL_WIND).percent
        woods = wind_effect(steps, RIDER, Wind(5, 0), lambda step, wind_from: 0.25).percent
        calm = wind_effect(steps, RIDER, Wind(5, 0), lambda step, wind_from: 0.0).percent
        assert 0 == pytest.approx(calm)
        assert 0 < woods < open_road

    def test_each_step_can_have_its_own_wind(self):
        steps = road(1000, 0, 0.0)
        one = wind_effect(steps, RIDER, Wind(5, 0), FULL_WIND).percent
        same = wind_effect(steps, RIDER, lambda step: Wind(5, 0), FULL_WIND).percent
        assert same == pytest.approx(one)

    def test_shelter_receives_the_wind_direction(self):
        seen = []

        def shelter(step, wind_from):
            seen.append(wind_from)
            return 1.0

        wind_effect(road(100, 0), RIDER, Wind(5, 123), shelter)
        assert set(seen) == {123}

    def test_air_density_comes_from_temperature_and_pressure(self):
        assert Wind(0, 0, 15, 1013.25).air_density == pytest.approx(1.225, abs=0.001)
        assert Wind(0, 0, 15, 700).air_density < Wind(0, 0, 15, 1013.25).air_density
        assert Wind(0, 0, 35, 1013.25).air_density < Wind(0, 0, 0, 1013.25).air_density

    def test_air_density_changes_the_effect(self):
        steps = road(2000, 0)
        sea_level = wind_effect(steps, RIDER, Wind(5, 0, 15, 1013), FULL_WIND).percent
        mountain = wind_effect(steps, RIDER, Wind(5, 0, 15, 700), FULL_WIND).percent
        assert abs(mountain - sea_level) > 1

    def test_tailwind_on_a_steep_descent_stays_under_the_speed_cap(self):
        effect = wind_effect(road(2000, 0, -0.15), RIDER, Wind(10, 180), FULL_WIND)
        assert effect.percent == pytest.approx(0, abs=0.01)
        assert effect.steep_descent

    def test_strong_tailwind_faster_than_the_rider_still_solves(self):
        assert wind_effect(road(2000, 0), RIDER, Wind(25, 180), FULL_WIND).percent < 0

    def test_gentle_roads_are_not_marked_as_steep_descents(self):
        assert not wind_effect(road(2000, 0, -0.02), RIDER, Wind(5, 0), FULL_WIND).steep_descent

    def test_matches_a_hand_worked_flat_road(self):
        # Solves the power equation separately from the module, by stepping up the speed.
        density = 1013.25 * 100 / (287.05 * 288.15)
        mass = 84.0
        steps = road(5000, 0)
        duration = 5000 / 9.5
        for _ in range(50):
            power = 200 - 20000 / 1200 + 20000 / max(duration, 60)
            speed = 0.0
            for _ in range(10000):
                speed += 0.002
                needed = (0.5 * density * 0.32 * speed**3 + mass * 9.81 * 0.004 * speed) / 0.97
                if needed >= power:
                    break
            duration = 5000 / speed
        windy = 0.0
        for _ in range(10000):
            windy += 0.002
            needed = (0.5 * density * 0.32 * (windy + 4) ** 2 * windy + mass * 9.81 * 0.004 * windy) / 0.97
            if needed >= power:
                break
        expected = (speed / windy - 1) * 100
        actual = wind_effect(steps, RIDER, Wind(4, 0), FULL_WIND).percent
        assert actual == pytest.approx(expected, abs=0.1)


class TestEffortPower:
    def test_twenty_minutes_gives_the_entered_power(self):
        assert effort_power(RIDER, 1200) == pytest.approx(200)

    def test_shorter_efforts_get_more_power(self):
        assert effort_power(RIDER, 300) > effort_power(RIDER, 1200) > effort_power(RIDER, 3600)

    def test_very_short_efforts_are_limited(self):
        assert effort_power(RIDER, 5) == effort_power(RIDER, 60)
