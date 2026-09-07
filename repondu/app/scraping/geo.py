"""Grille de points pour couvrir une ville (aucune dépendance externe)."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class City:
    name: str
    lat: float
    lng: float
    radius_km: float


def load_cities(path: Path) -> list[City]:
    with path.open(encoding="utf-8") as fh:
        return [
            City(row["city"].strip(), float(row["lat"]), float(row["lng"]), float(row["radius_km"]))
            for row in csv.DictReader(fh)
            if row.get("city")
        ]


def grid_points(city: City, step_km: float) -> list[tuple[float, float]]:
    """Points espacés de `step_km` couvrant le disque de rayon `radius_km` autour du centre.

    Le centre est toujours inclus. Les points sont arrondis à 5 décimales (≈1 m) pour rester
    stables entre deux exécutions (clé d'idempotence des jobs).
    """
    if step_km <= 0:
        raise ValueError("step_km doit être > 0")
    km_per_deg_lat = 111.32
    km_per_deg_lng = 111.32 * math.cos(math.radians(city.lat))
    n = int(math.ceil(city.radius_km / step_km))
    points: list[tuple[float, float]] = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            dy, dx = i * step_km, j * step_km
            if math.hypot(dx, dy) > city.radius_km + 1e-9:
                continue
            lat = round(city.lat + dy / km_per_deg_lat, 5)
            lng = round(city.lng + dx / km_per_deg_lng, 5)
            points.append((lat, lng))
    points.sort(key=lambda p: (abs(p[0] - city.lat) + abs(p[1] - city.lng), p))
    return points
