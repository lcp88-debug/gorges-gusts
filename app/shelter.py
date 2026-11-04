"""Estimates how much of the 10 m forecast wind reaches a rider on each step of a route.

Land cover and elevation are passed in as functions of (latitude, longitude), so this has
no map access of its own. The roughness values are typical published figures. The terrain
thresholds and limits are starting estimates and haven't been tuned against real rides.
"""

import math
from collections.abc import Callable, Sequence

from app.wind import ShelterFactor, Step

METERS_PER_DEGREE_LAT = 111_195
RIDER_HEIGHT_M = 1.2
FORECAST_HEIGHT_M = 10.0
DIRECTIONS = 16

# ESA WorldCover classes and their roughness length in metres. Trees and buildings use 0.5,
# below their usual values, because a road is a gap through them.
OPEN_ROUGHNESS_M = 0.03
ROUGHNESS_M = {
    10: 0.5,  # Trees
    20: 0.25,  # Shrubland
    30: OPEN_ROUGHNESS_M,  # Grassland
    40: 0.1,  # Cropland
    50: 0.5,  # Built-up
    60: 0.005,  # Bare ground
    70: 0.001,  # Snow and ice
    80: 0.0002,  # Open water
    90: OPEN_ROUGHNESS_M,  # Wetland
    95: 0.5,  # Mangroves
    100: 0.01,  # Moss and lichen
}
LAND_COVER_DISTANCES_M = (0, 25, 50, 75, 100)

# The elevation map includes treetops and roofs. Starting further out than the land cover
# check stops a 15 m tree beside the road from also counting as a hill.
TERRAIN_DISTANCES_M = tuple(range(200, 1601, 100))
SHELTER_STARTS_AT_SLOPE = 0.1
FULL_SHELTER_AT_SLOPE = 0.4
FULLY_SHELTERED_FACTOR = 0.3
RIDGE_SPEED_UP_PER_SLOPE = 1.6
MOST_RIDGE_SPEED_UP = 0.5

LESS_CERTAIN_SLOPE = 0.2
LESS_CERTAIN_SHARE_OF_STEPS = 0.1

LandCover = Callable[[float, float], int | None]
Elevation = Callable[[float, float], float | None]
ExposureTable = dict[Step, tuple[float, ...]]


def height_factor(roughness_m: float) -> float:
    """How much of the 10 m wind remains at rider height, using the logarithmic wind profile."""
    return math.log(RIDER_HEIGHT_M / roughness_m) / math.log(FORECAST_HEIGHT_M / roughness_m)


def _upwind(lat: float, lng: float, degrees: float, distance_m: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    north = distance_m * math.cos(angle)
    east = distance_m * math.sin(angle)
    return (
        lat + north / METERS_PER_DEGREE_LAT,
        lng + east / (METERS_PER_DEGREE_LAT * math.cos(math.radians(lat))),
    )


def _land_factor(step: Step, degrees: float, land_cover: LandCover) -> float:
    factors = []
    for distance in LAND_COVER_DISTANCES_M:
        kind = land_cover(*_upwind(step.lat, step.lng, degrees, distance))
        factors.append(height_factor(ROUGHNESS_M.get(kind, OPEN_ROUGHNESS_M)))
    return sum(factors) / len(factors)


def _upwind_slope(step: Step, degrees: float, elevation: Elevation) -> float | None:
    """The steepest rise towards high ground upwind. Negative if all of it is lower."""
    here = elevation(step.lat, step.lng)
    if here is None:
        return None
    slopes = []
    for distance in TERRAIN_DISTANCES_M:
        height = elevation(*_upwind(step.lat, step.lng, degrees, distance))
        if height is not None:
            slopes.append((height - here) / distance)
    return max(slopes, default=None)


def _terrain_factor(slope: float | None) -> float:
    if slope is None:
        return 1.0
    if slope < 0:
        return 1 + min(-slope * RIDGE_SPEED_UP_PER_SLOPE, MOST_RIDGE_SPEED_UP)
    share = (slope - SHELTER_STARTS_AT_SLOPE) / (FULL_SHELTER_AT_SLOPE - SHELTER_STARTS_AT_SLOPE)
    return 1 - min(max(share, 0.0), 1.0) * (1 - FULLY_SHELTERED_FACTOR)


def _directions() -> list[float]:
    return [index * 360 / DIRECTIONS for index in range(DIRECTIONS)]


def build_exposure(
    steps: Sequence[Step], land_cover: LandCover, elevation: Elevation
) -> ExposureTable:
    """Each step's share of the forecast wind, from each of 16 compass directions.

    It's derived from a Strava route, so it has to be deleted with the segment's details.
    """
    return {
        step: tuple(
            _land_factor(step, degrees, land_cover)
            * _terrain_factor(_upwind_slope(step, degrees, elevation))
            for degrees in _directions()
        )
        for step in steps
    }


def shelter_factor(table: ExposureTable) -> ShelterFactor:
    """Reads a table at any wind direction, blending the two nearest compass directions."""

    def factor(step: Step, wind_from: float) -> float:
        values = table[step]
        position = (wind_from % 360) / (360 / DIRECTIONS)
        below = int(position) % DIRECTIONS
        above = (below + 1) % DIRECTIONS
        share = position - int(position)
        return values[below] * (1 - share) + values[above] * share

    return factor


def steep_terrain(steps: Sequence[Step], elevation: Elevation) -> bool:
    """Part of the "less certain" mark: enough of the route has steep ground beside it."""
    steep = 0
    for step in steps:
        slopes = (_upwind_slope(step, degrees, elevation) for degrees in _directions())
        if any(slope is not None and abs(slope) >= LESS_CERTAIN_SLOPE for slope in slopes):
            steep += 1
    return steep >= len(steps) * LESS_CERTAIN_SHARE_OF_STEPS
