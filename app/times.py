"""Segment times as Strava writes them: m:ss, or h:mm:ss for an hour or more.

The browser reads and shows them for riders (web/src/rules/times.ts)."""


def parse_time(text: str) -> float:
    """"m:ss" or "h:mm:ss" as seconds. Raises ValueError for anything else."""
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(text)
    numbers = [int(part) for part in parts]
    if numbers[-1] > 59 or (len(numbers) == 3 and numbers[1] > 59):
        raise ValueError(text)
    total = 0
    for number in numbers:
        total = total * 60 + number
    return float(total)
