from dataclasses import dataclass

from http_utils import get_json

ECMWF_URL = "https://api.open-meteo.com/v1/ecmwf"

DAILY_VARIABLES = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum")


@dataclass(frozen=True)
class DailyForecast:
    date: str
    temp_max: float | None
    temp_min: float | None
    precipitation: float | None


class WeatherForecast:
    """Callable returning a daily ECMWF forecast from Open-Meteo.

    forecast = WeatherForecast(days=5)
    forecast((50.08, 14.42))  # (latitude, longitude) -> list[DailyForecast]

    Use Geocoder to get coordinates from a place name.
    """

    def __init__(self, days: int = 7, timeout: float = 10.0):
        if not 1 <= days <= 15:
            raise ValueError("days must be between 1 and 15")
        self.days = days
        self.timeout = timeout

    def __call__(self, coordinates: tuple[float, float]) -> list[DailyForecast]:
        latitude, longitude = coordinates
        data = get_json(
            ECMWF_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": ",".join(DAILY_VARIABLES),
                "timezone": "auto",
                "forecast_days": self.days,
            },
            self.timeout,
        )
        daily = data["daily"]
        return [
            DailyForecast(date, tmax, tmin, precip)
            for date, tmax, tmin, precip in zip(
                daily["time"],
                daily["temperature_2m_max"],
                daily["temperature_2m_min"],
                daily["precipitation_sum"],
            )
        ]
