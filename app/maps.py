"""Downloads whole map tiles and reads them locally, so routes never go to a map service.

A tile covers 1 to 3 degrees, so a download reveals only the broad region.
"""

import math
import os
from collections.abc import Callable
from pathlib import Path

import rasterio
import rasterio.errors
import rasterio.windows
import requests

from app.region import OutsideRegion, in_region

WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
ELEVATION_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
DOWNLOAD_TIMEOUT_SECONDS = 120
CHUNK_BYTES = 1024 * 1024
METERS_PER_DEGREE_LAT = 111_195


class MapsUnavailable(Exception):
    pass


def _hemispheres(lat: int, lng: int) -> tuple[str, str, str, str]:
    return ("N" if lat >= 0 else "S", f"{abs(lat):02d}", "E" if lng >= 0 else "W", f"{abs(lng):03d}")


def land_cover_tile(lat: float, lng: float) -> tuple[str, str]:
    """The file name and URL of the 3-degree land cover tile holding a location."""
    ns, lat_text, ew, lng_text = _hemispheres(
        math.floor(lat / 3) * 3, math.floor(lng / 3) * 3
    )
    name = f"ESA_WorldCover_10m_2021_v200_{ns}{lat_text}{ew}{lng_text}_Map.tif"
    return name, f"{WORLDCOVER_URL}/{name}"


def elevation_tile(lat: float, lng: float) -> tuple[str, str]:
    """The file name and URL of the 1-degree elevation tile holding a location."""
    ns, lat_text, ew, lng_text = _hemispheres(math.floor(lat), math.floor(lng))
    stem = f"Copernicus_DSM_COG_10_{ns}{lat_text}_00_{ew}{lng_text}_00_DEM"
    return f"{stem}.tif", f"{ELEVATION_URL}/{stem}/{stem}.tif"


class _Window:
    """One tile's pixels for an area, held in memory."""

    def __init__(self, path: Path, south: float, west: float, north: float, east: float):
        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            window = rasterio.windows.from_bounds(
                max(west, bounds.left),
                max(south, bounds.bottom),
                min(east, bounds.right),
                min(north, bounds.top),
                transform=dataset.transform,
            ).round_offsets(op="floor").round_lengths(op="ceil")
            self.pixels = dataset.read(1, window=window)
            self.nodata = dataset.nodata
            transform = dataset.window_transform(window)
        self.left, self.pixel_width = transform.c, transform.a
        self.top, self.pixel_height = transform.f, transform.e

    def value(self, lat: float, lng: float):
        column = math.floor((lng - self.left) / self.pixel_width)
        row = math.floor((lat - self.top) / self.pixel_height)
        rows, columns = self.pixels.shape
        if not (0 <= row < rows and 0 <= column < columns):
            return None
        value = self.pixels[row, column].item()
        return None if value == self.nodata else value


class MapLayer:
    def __init__(self, directory: str | os.PathLike, tile: Callable[[float, float], tuple[str, str]]):
        self.directory = Path(directory)
        self.tile = tile

    def _local_tile(self, lat: float, lng: float) -> Path | None:
        """The tile's path, downloading it if needed. None where no tile exists, as at sea."""
        name, url = self.tile(lat, lng)
        path = self.directory / name
        missing = path.with_name(f"{name}.missing")
        if path.exists():
            return path
        if missing.exists():
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{name}.partial")
        try:
            with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                if response.status_code == 404:
                    missing.touch()
                    return None
                response.raise_for_status()
                with partial.open("wb") as file:
                    for chunk in response.iter_content(CHUNK_BYTES):
                        file.write(chunk)
            partial.replace(path)
        except (requests.RequestException, OSError) as error:
            partial.unlink(missing_ok=True)
            raise MapsUnavailable(f"Couldn't download map tile {name}: {error}") from error
        return path

    def area(self, south: float, west: float, north: float, east: float):
        """A function giving this map's value at any location in the area, or None if unknown."""
        tiles: dict[Path, _Window] = {}
        step = 0.5
        lat = south
        while True:
            lng = west
            while True:
                path = self._local_tile(lat, lng)
                if path is not None and path not in tiles:
                    try:
                        tiles[path] = _Window(path, south, west, north, east)
                    except rasterio.errors.RasterioError as error:
                        raise MapsUnavailable(f"Couldn't read map tile {path.name}") from error
                if lng >= east:
                    break
                lng = min(lng + step, east)
            if lat >= north:
                break
            lat = min(lat + step, north)

        windows = list(tiles.values())

        def value_at(lat: float, lng: float):
            for window in windows:
                value = window.value(lat, lng)
                if value is not None:
                    return value
            return None

        return value_at


class Maps:
    """The land cover and elevation maps, stored in one folder."""

    def __init__(self, directory: str | os.PathLike):
        self.land_cover = MapLayer(directory, land_cover_tile)
        self.elevation = MapLayer(directory, elevation_tile)

    def for_route(self, points: list[tuple[float, float]], margin_m: float = 1800):
        """Land cover and elevation functions covering a route and the ground around it."""
        if not all(in_region(lat, lng) for lat, lng in points):
            raise OutsideRegion("Route is outside the region")
        lats = [lat for lat, _ in points]
        lngs = [lng for _, lng in points]
        margin_lat = margin_m / METERS_PER_DEGREE_LAT
        middle = math.radians(sum(lats) / len(lats))
        margin_lng = margin_lat / max(math.cos(middle), 0.1)
        bounds = (
            max(min(lats) - margin_lat, -90),
            max(min(lngs) - margin_lng, -180),
            min(max(lats) + margin_lat, 89.999),
            min(max(lngs) + margin_lng, 179.999),
        )
        return self.land_cover.area(*bounds), self.elevation.area(*bounds)
