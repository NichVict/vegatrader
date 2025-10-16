# bots/clube.py  (lógica pura; sem streamlit)
import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Lisbon")

def run_tick():
    """
    Tick leve do robô CLUBE.
    Aqui, em próximos passos, vamos:
      - ler estado no Supabase
      - buscar preços
      - atualizar acumuladores
      - salvar de volta
      - enviar alertas, se necessário
    """
    now = datetime.datetime.now(TZ)
    # Por enquanto, só devolve um “ok” com timestamp.
    return {"ok": True, "ts": now.isoformat()}
