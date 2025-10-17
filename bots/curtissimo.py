import datetime
import requests
from zoneinfo import ZoneInfo
import streamlit as st

TZ = ZoneInfo("Europe/Lisbon")

def _write_heartbeat():
    """Grava o heartbeat direto no Supabase (robô curtissimo)."""
    try:
        table = "kv_state_curtissimo"  # tabela específica do robô

        # Usa os secrets do Supabase (padrão: os do clube)
        supabase_url = st.secrets["supabase_url_cutissmo"]
        supabase_key = st.secrets["supabase_key_curtissimo"]

        now = datetime.datetime.utcnow().isoformat() + "Z"
        url = f"{supabase_url}/rest/v1/{table}"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": "heartbeat_curtissimo", "v": {"ts": now}}
        requests.post(url, headers=headers, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"[curtissimo] Erro ao gravar heartbeat: {e}")
        return False


def run_tick():
    """Executa o tick deste robô (CURTÍSSIMO)."""
    now = datetime.datetime.now(TZ)
    _write_heartbeat()  # grava o heartbeat direto no Supabase
    return {"ok": True, "ts": now.isoformat()}
