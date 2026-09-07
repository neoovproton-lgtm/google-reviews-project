import math
from pathlib import Path

from app.scraping.geo import City, grid_points, load_cities


def test_load_cities_default_file():
    cities = load_cities(Path(__file__).parent.parent / "data" / "cities.csv")
    assert len(cities) == 10
    assert cities[0].name == "Paris"


def test_grid_includes_center_and_stays_in_radius():
    city = City("Test", 45.0, 5.0, 3.0)
    pts = grid_points(city, 1.0)
    assert pts[0] == (45.0, 5.0)
    assert len(pts) > 20
    for lat, lng in pts:
        dy = (lat - city.lat) * 111.32
        dx = (lng - city.lng) * 111.32 * math.cos(math.radians(city.lat))
        assert math.hypot(dx, dy) <= city.radius_km + 0.01


def test_grid_is_deterministic():
    city = City("Test", 48.8566, 2.3522, 6.0)
    assert grid_points(city, 1.2) == grid_points(city, 1.2)


def test_grid_small_radius_single_point():
    assert grid_points(City("X", 0.0, 0.0, 0.1), 1.0) == [(0.0, 0.0)]
