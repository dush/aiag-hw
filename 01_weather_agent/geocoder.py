from dataclasses import dataclass

from http_utils import get_json

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float


class Geocoder:
    """Callable resolving a place name to a Location via Open-Meteo geocoding.

    Geocoder()("Prague")
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def __call__(self, name: str) -> Location:
        data = get_json(GEOCODING_URL, {"name": name, "count": 1}, self.timeout)
        results = data.get("results")
        if not results:
            raise LookupError(f"location not found: {name!r}")
        best = results[0]
        label = f"{best['name']}, {best.get('country', '')}".rstrip(", ")
        return Location(label, best["latitude"], best["longitude"])
