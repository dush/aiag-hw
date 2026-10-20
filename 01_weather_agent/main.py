import logging
import os

from agent import Agent
from geocoder import Geocoder
from my_location import MyLocation
from tool import Tool
from weather_forecast import WeatherForecast

INSTRUCTIONS = (
    "You are a weather assistant. Use the geocoder tool to turn a place name "
    "into coordinates, then the weather forecast tool to get the forecast. "
    "If the user does not name a place (e.g. 'here', 'my location'), use the "
    "IP locator tool to find the approximate current location first. "
    "Answer concisely."
)


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    agent = Agent(
        instructions=INSTRUCTIONS,
        tools=[Tool(Geocoder()), Tool(MyLocation()), Tool(WeatherForecast())],
    )
    prompt = input("Ask for a weather forecast (e.g. 'Weather in Prague' or 'Weather here'): ")
    response = agent.ask(prompt)
    print(response.content)


if __name__ == "__main__":
    main()
