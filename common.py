from datetime import datetime
from dateutil import tz

def get_current_utc() -> str:
    current_utc = datetime.now(tz=tz.UTC)
    formatted_time = current_utc.strftime('%Y-%m-%d %H:%M:%S')
    return formatted_time
