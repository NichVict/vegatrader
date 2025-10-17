import datetime
import requests
from zoneinfo import ZoneInfo
import streamlit as st

TZ = ZoneInfo("Europe/Lisbon")

def _write_heartbeat():
    """Grava o heartbeat direto no Supabase (robô loss_curto)."""
    try:
        table = "kv_state_losscurto"  # tabela específica do robô

        # Usa os mesmos secrets do Supabase (do clube)
        supabase_url = st.secrets["supabase_url_loss_curto"]
        supabase_key = st.secrets["supabase_key_loss_curto"]

        now = datetime.datetime.utcnow().isoformat() + "Z"
        url = f"{supabase_url}/rest/v1/{table}"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": "heartbeat_loss_curto", "v": {"ts": now}}
        requests.post(url, headers=headers, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"[loss_curto] Erro ao gravar heartbeat: {e}")
        return False


def run_tick():
    """Executa o tick deste robô."""
    now = datetime.datetime.now(TZ)
    _write_heartbeat()  # grava o heartbeat direto no Supabase
    return {"ok": True, "ts": now.isoformat()}
