"""All routes and maps here are made up."""

import pytest

from app.shelter import (
    METERS_PER_DEGREE_LAT,
    build_exposure,
    height_factor,
    shelter_factor,
    steep_terrain,
)
from app.wind import RiderProfile, Step, Wind, wind_effect

LAT, LNG = 45.0, -120.0
STEP = Step(LAT, LNG, 25.0, 0.0, 0.0)
GRASS, TREES, WATER = 30, 10, 80
NORTH, EAST, SOUTH = 0.0, 90.0, 180.0


def flat(lat, lng):
    return 100.0


def all_grass(lat, lng):
    return GRASS


def factor(land=all_grass, elevation=flat, wind_from=NORTH, step=STEP):
    table = build_exposure([step], land, elevation)
    return shelter_factor(table)(step, wind_from)


def meters_north(lat):
    return (lat - LAT) * METERS_PER_DEGREE_LAT


class TestHeightFactor:
    @pytest.mark.parametrize(
        "roughness, low, high", [(0.0002, 0.75, 0.85), (0.03, 0.6, 0.68), (0.5, 0.25, 0.33)]
    )
    def test_matches_the_logarithmic_profile(self, roughness, low, high):
        assert low < height_factor(roughness) < high

    def test_rougher_ground_leaves_less_wind(self):
        assert height_factor(0.5) < height_factor(0.1) < height_factor(0.03)


class TestLandCover:
    def test_open_grass_uses_its_own_factor(self):
        assert factor() == pytest.approx(height_factor(0.03))

    def test_trees_shelter_more_than_grass_and_water_less(self):
        trees = factor(land=lambda lat, lng: TREES)
        water = factor(land=lambda lat, lng: WATER)
        assert trees < factor() < water

    def test_only_land_upwind_counts(self):
        def trees_to_the_north(lat, lng):
            return TREES if lat > LAT else GRASS

        assert factor(trees_to_the_north, wind_from=NORTH) < factor(
            trees_to_the_north, wind_from=SOUTH
        )
        assert factor(trees_to_the_north, wind_from=SOUTH) == pytest.approx(factor())

    def test_land_beyond_100_m_upwind_is_ignored(self):
        def distant_trees(lat, lng):
            return TREES if meters_north(lat) > 110 else GRASS

        assert factor(distant_trees) == pytest.approx(factor())

    def test_missing_or_unknown_land_counts_as_open(self):
        assert factor(land=lambda lat, lng: None) == pytest.approx(factor())
        assert factor(land=lambda lat, lng: 999) == pytest.approx(factor())


class TestTerrain:
    def test_a_big_hill_upwind_shelters_only_that_direction(self):
        def hill_to_the_north(lat, lng):
            return 500.0 if meters_north(lat) > 500 else 100.0

        assert factor(elevation=hill_to_the_north, wind_from=NORTH) == pytest.approx(
            factor() * 0.3
        )
        assert factor(elevation=hill_to_the_north, wind_from=EAST) == pytest.approx(factor())

    def test_a_gentle_rise_gives_no_shelter(self):
        def gentle(lat, lng):
            return 100 + meters_north(lat) * 0.05

        assert factor(elevation=gentle) == pytest.approx(factor())

    def test_shelter_grows_with_the_slope(self):
        def slope_of(rise):
            return lambda lat, lng: 100 + meters_north(lat) * rise

        assert factor(elevation=slope_of(0.35)) < factor(elevation=slope_of(0.2)) < factor()

    def test_a_ridge_top_gets_more_wind(self):
        def ridge(lat, lng):
            return 100 - abs(meters_north(lat)) * 0.1

        assert factor(elevation=ridge) == pytest.approx(factor() * 1.16, abs=0.03)

    def test_ridge_speed_up_is_limited(self):
        def cliff_top(lat, lng):
            return 1000 - abs(meters_north(lat)) * 5

        assert factor(elevation=cliff_top) == pytest.approx(factor() * 1.5)

    def test_missing_elevation_makes_no_change(self):
        assert factor(elevation=lambda lat, lng: None) == pytest.approx(factor())

    def test_a_tree_beside_the_road_does_not_count_as_a_hill(self):
        def tree(lat, lng):
            return 115.0 if 0 < meters_north(lat) < 60 else 100.0

        assert factor(elevation=tree) == pytest.approx(factor())


class TestShelterFactor:
    def test_blends_between_compass_directions(self):
        def land(lat, lng):
            return TREES if lat > LAT else GRASS

        table = build_exposure([STEP], land, flat)
        read = shelter_factor(table)
        north, next_along = table[STEP][0], table[STEP][1]
        assert read(STEP, 11.25) == pytest.approx((north + next_along) / 2)

    def test_wraps_around_north(self):
        table = {STEP: tuple(float(index) for index in range(16))}
        read = shelter_factor(table)
        assert read(STEP, 360) == 0
        assert read(STEP, 348.75) == pytest.approx(7.5)

    def test_plugs_into_the_wind_effect(self):
        steps = [Step(LAT, LNG, 2000.0, 0.0, 0.0)]
        rider = RiderProfile()
        wind = Wind(8, NORTH)
        open_road = build_exposure(steps, all_grass, flat)
        woods = build_exposure(steps, lambda lat, lng: TREES, flat)
        exposed = wind_effect(steps, rider, wind, shelter_factor(open_road)).percent
        sheltered = wind_effect(steps, rider, wind, shelter_factor(woods)).percent
        assert 0 < sheltered < exposed


class TestSteepTerrain:
    def test_flat_ground_is_not_steep(self):
        assert not steep_terrain([STEP], flat)

    def test_a_steep_valley_side_is_steep(self):
        def valley(lat, lng):
            return 100 + abs(meters_north(lat)) * 0.3

        assert steep_terrain([STEP], valley)

    def test_a_little_steep_ground_on_a_long_route_is_not_enough(self):
        far = 45.5
        steps = [STEP] + [Step(far, LNG + index * 0.01, 25.0, 0.0, 0.0) for index in range(19)]

        def hill_near_the_start(lat, lng):
            return 100 + abs(meters_north(lat)) * 0.3 if lat < 45.1 else 100.0

        assert not steep_terrain(steps, hill_near_the_start)

    def test_missing_elevation_is_not_steep(self):
        assert not steep_terrain([STEP], lambda lat, lng: None)
