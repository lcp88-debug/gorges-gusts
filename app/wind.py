"""Works out a segment's wind effect: the % change in time at the same effort, compared with no wind.

Everything here is plain calculation, with no database, network or map access. The
per-step shelter factor and elevations are passed in by the caller.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000
GRAVITY = 9.81
GAS_CONSTANT_DRY_AIR = 287.05

STEP_M = 25.0
BEARING_WINDOW_M = 50.0
GRADE_WINDOW_M = 150.0

BIKE_KG = 9.0
CDA = 0.32
ROLLING_RESISTANCE = 0.004
DRIVETRAIN_EFFICIENCY = 0.97
STANDARD_AIR_DENSITY = 1.225

DEFAULT_WEIGHT_KG = 75.0
DEFAULT_POWER_20MIN_W = 200.0
# Extra work a rider can do above their sustainable power (a typical figure), and the
# shortest effort it's applied to, so very short segments don't get absurd power.
ANAEROBIC_WORK_J = 20_000.0
SHORTEST_EFFORT_S = 60.0

MAX_SPEED_MS = 22.0  # About 49 mph: braking and bends take over above this.
STEEP_DESCENT_GRADE = -0.06


@dataclass(frozen=True)
class RiderProfile:
    weight_kg: float = DEFAULT_WEIGHT_KG
    power_20min_w: float = DEFAULT_POWER_20MIN_W


@dataclass(frozen=True)
class Wind:
    speed_ms: float  # At 10 m, as forecast.
    from_degrees: float  # The direction it blows from, as forecast.
    temperature_c: float = 15.0
    pressure_hpa: float = 1013.25

    @property
    def air_density(self) -> float:
        kelvin = self.temperature_c + 273.15
        return self.pressure_hpa * 100 / (GAS_CONSTANT_DRY_AIR * kelvin)


@dataclass(frozen=True)
class Step:
    lat: float
    lng: float
    length_m: float
    bearing: float  # Degrees clockwise from north, in the direction of travel.
    grade: float  # Rise over run, so 0.07 is 7%.


@dataclass(frozen=True)
class WindEffect:
    percent: float
    steep_descent: bool  # Part of the "less certain" mark.


# Given a step and the direction the wind comes from, how much of the 10 m forecast
# speed reaches the rider. Land cover and high ground are handled by the caller.
ShelterFactor = Callable[[Step, float], float]


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decodes an encoded polyline into (latitude, longitude) points."""
    points = []
    position = lat = lng = 0
    while position < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                if position >= len(encoded):
                    raise ValueError("Polyline ended part-way through a point")
                chunk = ord(encoded[position]) - 63
                position += 1
                result |= (chunk & 0x1F) << shift
                shift += 5
                if chunk < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat_a, lat_b = math.radians(a[0]), math.radians(b[0])
    half_lat = (lat_b - lat_a) / 2
    half_lng = math.radians(b[1] - a[1]) / 2
    h = math.sin(half_lat) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(half_lng) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat_a, lat_b = math.radians(a[0]), math.radians(b[0])
    delta_lng = math.radians(b[1] - a[1])
    x = math.sin(delta_lng) * math.cos(lat_b)
    y = math.cos(lat_a) * math.sin(lat_b) - math.sin(lat_a) * math.cos(lat_b) * math.cos(delta_lng)
    return math.degrees(math.atan2(x, y)) % 360


class _Path:
    """A route that can be read at any distance along it."""

    def __init__(self, points: Sequence[tuple[float, float]]):
        self.points = [points[0]]
        self.distances = [0.0]
        for point in points[1:]:
            gap = _distance_m(self.points[-1], point)
            if gap > 0:  # Repeated points have no direction.
                self.points.append(point)
                self.distances.append(self.distances[-1] + gap)
        self.length = self.distances[-1]

    def at(self, distance: float) -> tuple[float, float]:
        distance = min(max(distance, 0.0), self.length)
        low, high = 0, len(self.distances) - 1
        while high - low > 1:
            middle = (low + high) // 2
            if self.distances[middle] <= distance:
                low = middle
            else:
                high = middle
        if high == low:
            return self.points[low]
        share = (distance - self.distances[low]) / (self.distances[high] - self.distances[low])
        a, b = self.points[low], self.points[high]
        return (a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share)


def build_steps(
    points: Sequence[tuple[float, float]],
    average_grade: float,
    elevation_at: Callable[[float, float], float] | None = None,
) -> list[Step]:
    """Walks a route in short steps, smoothing direction and gradient over longer distances.

    `average_grade` is rise over run. Without `elevation_at`, every step uses it. With it,
    the gradient follows the elevation map, shifted so it averages out to `average_grade`.
    """
    if len(points) < 2:
        raise ValueError("A route needs at least two points")
    path = _Path(points)
    if path.length == 0:
        raise ValueError("A route needs some length")

    count = max(1, round(path.length / STEP_M))
    length = path.length / count
    centres = [(index + 0.5) * length for index in range(count)]

    grades = [average_grade] * count
    if elevation_at is not None:
        grades = _map_grades(path, centres, average_grade, elevation_at)

    steps = []
    for centre, grade in zip(centres, grades):
        behind = path.at(centre - BEARING_WINDOW_M / 2)
        ahead = path.at(centre + BEARING_WINDOW_M / 2)
        lat, lng = path.at(centre)
        steps.append(Step(lat, lng, length, _bearing(behind, ahead), grade))
    return steps


def _map_grades(
    path: _Path,
    centres: Sequence[float],
    average_grade: float,
    elevation_at: Callable[[float, float], float],
) -> list[float]:
    half = GRADE_WINDOW_M / 2
    grades = []
    for centre in centres:
        start = max(centre - half, 0.0)
        end = min(centre + half, path.length)
        rise = elevation_at(*path.at(end)) - elevation_at(*path.at(start))
        grades.append(rise / (end - start))
    shift = average_grade - sum(grades) / len(grades)
    return [grade + shift for grade in grades]


def effort_power(profile: RiderProfile, duration_s: float) -> float:
    """The power a rider can hold for `duration_s`, based on their best 20 minutes."""
    sustainable = profile.power_20min_w - ANAEROBIC_WORK_J / 1200
    return sustainable + ANAEROBIC_WORK_J / max(duration_s, SHORTEST_EFFORT_S)


def _power_needed(
    speed: float, headwind: float, crosswind: float, grade: float, mass: float, density: float
) -> float:
    along = speed + headwind
    apparent = math.hypot(along, crosswind)
    drag = 0.5 * density * CDA * apparent * along
    slope = math.atan(grade)
    resistance = mass * GRAVITY * (math.sin(slope) + ROLLING_RESISTANCE * math.cos(slope))
    return (drag + resistance) * speed / DRIVETRAIN_EFFICIENCY


def _solve_speed(
    power: float, headwind: float, crosswind: float, grade: float, mass: float, density: float
) -> float:
    """The speed where the power needed equals `power`, capped at MAX_SPEED_MS."""
    if _power_needed(MAX_SPEED_MS, headwind, crosswind, grade, mass, density) <= power:
        return MAX_SPEED_MS
    low, high = 0.0, MAX_SPEED_MS
    for _ in range(60):
        middle = (low + high) / 2
        if _power_needed(middle, headwind, crosswind, grade, mass, density) < power:
            low = middle
        else:
            high = middle
    return high


def _time_s(
    steps: Sequence[Step],
    power: float,
    mass: float,
    wind: Wind | None,
    shelter: ShelterFactor,
    density: float,
) -> float:
    total = 0.0
    for step in steps:
        headwind = crosswind = 0.0
        if wind is not None:
            at_rider = wind.speed_ms * shelter(step, wind.from_degrees)
            angle = math.radians(wind.from_degrees - step.bearing)
            headwind = at_rider * math.cos(angle)
            crosswind = at_rider * math.sin(angle)
        total += step.length_m / _solve_speed(power, headwind, crosswind, step.grade, mass, density)
    return total


def wind_effect(
    steps: Sequence[Step], profile: RiderProfile, wind: Wind, shelter: ShelterFactor
) -> WindEffect:
    """The % change in the rider's time at the power they'd hold with no wind."""
    mass = profile.weight_kg + BIKE_KG
    density = wind.air_density

    # A rider's power depends on how long the effort lasts, which depends on their power.
    power = profile.power_20min_w
    for _ in range(20):
        duration = _time_s(steps, power, mass, None, shelter, density)
        power = effort_power(profile, duration)

    still = _time_s(steps, power, mass, None, shelter, density)
    windy = _time_s(steps, power, mass, wind, shelter, density)
    return WindEffect(
        percent=(windy / still - 1) * 100,
        steep_descent=any(step.grade <= STEEP_DESCENT_GRADE for step in steps),
    )
