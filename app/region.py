"""The one area the app works out wind for: Tompkins County, NY, and the land around it."""

from app.wind import decode_polyline

SOUTH, NORTH = 42.05, 42.95
WEST, EAST = -77.05, -75.95


class OutsideRegion(ValueError):
    """Something tried to use weather or map data for a place outside the region."""


def in_region(lat: float, lng: float, padding: float = 0.0) -> bool:
    return SOUTH - padding <= lat <= NORTH + padding and WEST - padding <= lng <= EAST + padding


def route_in_region(polyline: str | None) -> bool:
    """Whether a whole encoded route is inside. A route that can't be read doesn't count."""
    if not polyline:
        return False
    try:
        points = decode_polyline(polyline)
    except ValueError:
        return False
    return bool(points) and all(in_region(lat, lng) for lat, lng in points)
