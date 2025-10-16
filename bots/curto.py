# bots/curto.py  (lógica pura; sem streamlit)
import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Lisbon")

def run_tick():
    """
    Tick leve do robô CURTO.
    (Próximos passos: ligar Supabase, preços, regras e alertas.)
    """
    now = datetime.datetime.now(TZ)
    # por enquanto só retornamos um carimbo de data/hora
    return {"ok": True, "ts": now.isoformat()}
