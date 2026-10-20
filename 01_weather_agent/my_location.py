from geocoder import Location
from http_utils import get_json

IP_GEOLOCATION_URL = "https://ipwho.is/" # https://ipwhois.io/documentation


class MyLocation:
    """Callable returning the approximate current location (city level) of this machine.

    Use it when the user asks about "here" or "my location" without naming a place.

    MyLocation()()  # -> Location(name="Prague, Czechia", latitude=..., longitude=...)
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def __call__(self) -> Location:
        data = get_json(IP_GEOLOCATION_URL, {}, self.timeout)
        if not data.get("success"):
            raise LookupError(f"could not locate IP address: {data.get('message', 'unknown error')}")
        label = ", ".join(part for part in (data.get("city"), data.get("country")) if part)
        return Location(label, data["latitude"], data["longitude"])
