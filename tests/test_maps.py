"""The map tiles here are small made-up files, not real maps."""

import re

import numpy as np
import pytest
import rasterio
import requests
import responses
from rasterio.transform import from_origin

from app.region import EAST, NORTH, SOUTH, WEST, OutsideRegion
from app.maps import MapLayer, Maps, MapsUnavailable, elevation_tile, land_cover_tile

PIXEL = 0.01


def write_tile(path, west, north, values, nodata=None):
    array = np.array(values, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, PIXEL, PIXEL),
        nodata=nodata,
    ) as dataset:
        dataset.write(array, 1)


def fixed_tile(name):
    return lambda lat, lng: (name, f"https://maps.example/{name}")


class TestTileNames:
    @pytest.mark.parametrize(
        "lat, lng, expected",
        [
            (45.5, -120.5, "N45W123"),
            (47.9, -123.0, "N45W123"),
            (-0.5, -0.5, "S03W003"),
            (51.0, 7.0, "N51E006"),
        ],
    )
    def test_land_cover_uses_three_degree_tiles(self, lat, lng, expected):
        name, url = land_cover_tile(lat, lng)
        assert name == f"ESA_WorldCover_10m_2021_v200_{expected}_Map.tif"
        assert url.endswith(f"/map/{name}")

    @pytest.mark.parametrize(
        "lat, lng, expected",
        [(45.5, -120.5, "N45_00_W121_00"), (-0.5, 0.5, "S01_00_E000_00")],
    )
    def test_elevation_uses_one_degree_tiles(self, lat, lng, expected):
        name, url = elevation_tile(lat, lng)
        stem = f"Copernicus_DSM_COG_10_{expected}_DEM"
        assert name == f"{stem}.tif"
        assert url.endswith(f"/{stem}/{stem}.tif")


class TestReading:
    def test_reads_the_pixel_holding_a_location(self, tmp_path):
        write_tile(tmp_path / "t.tif", -120.0, 45.03, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        layer = MapLayer(tmp_path, fixed_tile("t.tif"))
        value = layer.area(45.0, -120.0, 45.03, -119.97)
        assert value(45.025, -119.995) == 1
        assert value(45.015, -119.985) == 5
        assert value(45.005, -119.975) == 9

    def test_outside_the_tile_is_unknown(self, tmp_path):
        write_tile(tmp_path / "t.tif", -120.0, 45.03, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        value = MapLayer(tmp_path, fixed_tile("t.tif")).area(45.0, -120.0, 45.03, -119.97)
        assert value(46.0, -119.985) is None

    def test_no_data_pixels_are_unknown(self, tmp_path):
        write_tile(tmp_path / "t.tif", -120.0, 45.01, [[-1, 2]], nodata=-1)
        value = MapLayer(tmp_path, fixed_tile("t.tif")).area(45.0, -120.0, 45.01, -119.98)
        assert value(45.005, -119.995) is None
        assert value(45.005, -119.985) == 2

    def test_reads_across_two_tiles(self, tmp_path):
        write_tile(tmp_path / "west.tif", -120.02, 45.01, [[1, 1]])
        write_tile(tmp_path / "east.tif", -120.0, 45.01, [[2, 2]])

        def tile(lat, lng):
            name = "west.tif" if lng < -120.0 else "east.tif"
            return name, f"https://maps.example/{name}"

        value = MapLayer(tmp_path, tile).area(45.0, -120.02, 45.01, -119.98)
        assert value(45.005, -120.015) == 1
        assert value(45.005, -119.985) == 2

    def test_for_route_covers_the_ground_around_it(self, tmp_path):
        values = np.arange(100).reshape(10, 10)
        write_tile(tmp_path / "t.tif", -76.55, 42.5, values)
        maps = Maps(tmp_path)
        maps.land_cover.tile = maps.elevation.tile = fixed_tile("t.tif")
        land, elevation = maps.for_route([(42.45, -76.5)], margin_m=2000)
        assert land(42.45 + 0.015, -76.5) is not None
        assert elevation(42.45 - 0.015, -76.5) is not None


class TestRegion:
    @responses.activate
    @pytest.mark.parametrize(
        "route",
        [[(44.0, -120.0)], [(42.4, -76.5), (42.4, -75.9)], [(42.94, -76.5), (42.96, -76.5)]],
    )
    def test_refuses_routes_outside_without_any_download(self, tmp_path, route):
        with pytest.raises(OutsideRegion):
            Maps(tmp_path).for_route(route)
        assert len(responses.calls) == 0

    @responses.activate
    def test_the_whole_region_needs_only_four_tiles(self, tmp_path):
        responses.get(re.compile(r"https://.*"), status=404)
        Maps(tmp_path).for_route([(SOUTH, WEST), (NORTH, EAST)])
        assert sorted(path.name.removesuffix(".tif.missing") for path in tmp_path.iterdir()) == [
            "Copernicus_DSM_COG_10_N42_00_W076_00_DEM",
            "Copernicus_DSM_COG_10_N42_00_W077_00_DEM",
            "Copernicus_DSM_COG_10_N42_00_W078_00_DEM",
            "ESA_WorldCover_10m_2021_v200_N42W078_Map",
        ]


class TestDownloading:
    @responses.activate
    def test_downloads_a_tile_once(self, tmp_path):
        source = tmp_path / "source.tif"
        write_tile(source, -120.0, 45.01, [[7]])
        responses.get("https://maps.example/t.tif", body=source.read_bytes())
        folder = tmp_path / "maps"
        layer = MapLayer(folder, fixed_tile("t.tif"))
        assert layer.area(45.0, -120.0, 45.01, -119.99)(45.005, -119.995) == 7
        layer.area(45.0, -120.0, 45.01, -119.99)
        assert len(responses.calls) == 1
        assert [path.name for path in folder.iterdir()] == ["t.tif"]

    @responses.activate
    def test_a_missing_tile_is_remembered(self, tmp_path):
        responses.get("https://maps.example/sea.tif", status=404)
        layer = MapLayer(tmp_path, fixed_tile("sea.tif"))
        assert layer.area(45.0, -120.0, 45.01, -119.99)(45.005, -119.995) is None
        layer.area(45.0, -120.0, 45.01, -119.99)
        assert len(responses.calls) == 1

    @responses.activate
    @pytest.mark.parametrize("reply", [{"status": 500}, {"body": requests.ConnectionError()}])
    def test_a_failed_download_leaves_nothing_behind(self, tmp_path, reply):
        responses.get("https://maps.example/t.tif", **reply)
        layer = MapLayer(tmp_path, fixed_tile("t.tif"))
        with pytest.raises(MapsUnavailable):
            layer.area(45.0, -120.0, 45.01, -119.99)
        assert list(tmp_path.iterdir()) == []

    @responses.activate
    def test_requests_carry_only_the_tile_name(self, tmp_path):
        responses.get("https://maps.example/sea.tif", status=404)
        MapLayer(tmp_path, fixed_tile("sea.tif")).area(45.0, -120.0, 45.01, -119.99)
        assert responses.calls[0].request.url == "https://maps.example/sea.tif"

    def test_a_corrupt_tile_raises_a_clear_error(self, tmp_path):
        (tmp_path / "t.tif").write_bytes(b"not a map")
        with pytest.raises(MapsUnavailable):
            MapLayer(tmp_path, fixed_tile("t.tif")).area(45.0, -120.0, 45.01, -119.99)
