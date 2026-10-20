import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def get_json(url: str, params: dict, timeout: float) -> dict:
    try:
        with urlopen(f"{url}?{urlencode(params)}", timeout=timeout) as response:
            return json.load(response)
    except URLError as error:
        raise ConnectionError(f"request to {url} failed: {error}") from error
