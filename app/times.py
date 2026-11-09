"""Segment times as riders read and type them: m:ss, or h:mm:ss for an hour or more."""


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


def format_time(seconds: float) -> str:
    """Seconds as m:ss, or h:mm:ss for an hour or more."""
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"
