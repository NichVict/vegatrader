import datetime
import requests
from zoneinfo import ZoneInfo
import streamlit as st

TZ = ZoneInfo("Europe/Lisbon")

def _write_heartbeat():
    """Grava o heartbeat direto no Supabase (robô clube)."""
    try:
        table = "kv_state_clube"  # tabela específica do robô

        # Usa os secrets do Supabase
        supabase_url = st.secrets["supabase_url_clube"]
        supabase_key = st.secrets["supabase_key_clube"]

        now = datetime.datetime.utcnow().isoformat() + "Z"
        url = f"{supabase_url}/rest/v1/{table}"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": "heartbeat_clube", "v": {"ts": now}}
        requests.post(url, headers=headers, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"[clube] Erro ao gravar heartbeat: {e}")
        return False


def run_tick():
    """
    Tick leve do robô CLUBE.
    Por enquanto, apenas grava heartbeat e devolve timestamp.
    """
    now = datetime.datetime.now(TZ)
    _write_heartbeat()  # envia o heartbeat direto ao Supabase
    return {"ok": True, "ts": now.isoformat()}

