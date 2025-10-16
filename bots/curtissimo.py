import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Europe/Lisbon")
def run_tick():
    now = datetime.datetime.now(TZ)
    return {"ok": True, "ts": now.isoformat()}
