from datetime import datetime, timezone


def utcnow() -> str:
    current_utc = datetime.now(tz=timezone.utc)
    formatted_time = current_utc.strftime('%Y-%m-%dT%H:%M:%S')
    return formatted_time
