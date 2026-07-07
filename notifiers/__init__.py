"""Alert notifiers — Telegram and Discord."""
from . import telegram, discord


def send(channels: list[str], title: str, message: str) -> dict:
    """Fan out one alert to the selected channels. Returns per-channel result."""
    results = {}
    if "telegram" in channels:
        results["telegram"] = telegram.send(title, message)
    if "discord" in channels:
        results["discord"] = discord.send(title, message)
    return results
