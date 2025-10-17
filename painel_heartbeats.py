# -*- coding: utf-8 -*-
"""
Painel de Heartbeats — Robôs 1Milhão

Monitora os heartbeats dos robôs e permite forçar pings globais
para manter o Streamlit ativo e sincronizar o Supabase.
"""

import datetime
import time
from zoneinfo import ZoneInfo
import streamlit as st
import pandas as pd
import requests

# ===============================
# CONFIGURAÇÃO
# ===============================
st.set_page_config(page_title="Heartbeats — 1Milhão", page_icon="💓", layout="wide")
_TZ = ZoneInfo("Europe/Lisbon")

ROBOS = [
    "curto", "curtissimo", "clube",
    "loss_curto", "loss_curtissimo", "loss_clube"
]

TABLE_MAP = {
    "curto": "kv_state_curto",
    "curtissimo": "kv_state_curtissimo",
    "clube": "kv_state_clube",
    "loss_curto": "kv_state_losscurto",
    "loss_curtissimo": "kv_state_losscurtissimo",
    "loss_clube": "kv_state_lossclube",
}

HBKEY_MAP = {
    "curto": "heartbeat_curto",
    "curtissimo": "heartbeat_curtissimo",
    "clube": "heartbeat_clube",
    "loss_curto": "heartbeat_loss_curto",
    "loss_curtissimo": "heartbeat_loss_curtissimo",
    "loss_clube": "heartbeat_loss_clube",
}

def _get_supabase_creds(key: str):
    """Retorna (url, key) a partir de st.secrets"""
    url_name = f"supabase_url_{key}" if f"supabase_url_{key}" in st.secrets else "supabase_url_clube"
    key_name = f"supabase_key_{key}" if f"supabase_key_{key}" in st.secrets else "supabase_key_clube"
    return st.secrets[url_name], st.secrets[key_name]

PAINEL_URL = st.secrets.get("painel_url", None)  # Ex: https://painel-1milhao.streamlit.app


# ===============================
# FUNÇÕES AUXILIARES
# ===============================
def get_heartbeat_info(key: str):
    """Retorna ts do JSON e updated_at"""
    try:
        supabase_url, supabase_key = _get_supabase_creds(key)
        table = TABLE_MAP[key]
        hb_key = HBKEY_MAP[key]
        url = f"{supabase_url}/rest/v1/{table}?k=eq.{hb_key}&select=k,v,updated_at"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200 or not r.json():
            return None, None
        row = r.json()[0]
        vts = row["v"].get("ts") if isinstance(row["v"], dict) else None
        updated_at = row.get("updated_at")
        return vts, updated_at
    except Exception as e:
        return f"ERRO: {e}", None


def force_global_ping():
    """Envia ?ping=all ao painel principal"""
    if not PAINEL_URL:
        st.error("⚠️ 'painel_url' não configurado em st.secrets.")
        return False
    try:
        url = f"{PAINEL_URL}?ping=all"
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            st.success(f"Ping global executado com sucesso ({r.text.strip()}) ✅")
            return True
        else:
            st.error(f"Falha no ping: HTTP {r.status_code} — {r.text}")
            return False
    except Exception as e:
        st.error(f"Erro ao enviar ping: {e}")
        return False


def montar_dataframe():
    """Consulta o Supabase e retorna dataframe de status"""
    data = []
    now = datetime.datetime.now(_TZ)

    for key in ROBOS:
        vts, upd = get_heartbeat_info(key)
        if vts is None and upd is None:
            data.append({
                "Robô": key,
                "Último v.ts": "—",
                "updated_at": "—",
                "Atraso (min)": "—",
                "Status": "❌ Sem dados"
            })
            continue

        try:
            ts_dt = datetime.datetime.fromisoformat(vts.replace("Z", "+00:00")).astimezone(_TZ) if vts else None
            up_dt = datetime.datetime.fromisoformat(upd).astimezone(_TZ) if upd else None
            ref_dt = up_dt or ts_dt
            delay = (now - ref_dt).total_seconds() / 60 if ref_dt else None
            status = "🟢 OK" if delay is not None and delay < 10 else ("🟡 Lento" if delay < 30 else "🔴 Inativo")

            data.append({
                "Robô": key,
                "Último v.ts": ts_dt.strftime("%Y-%m-%d %H:%M:%S") if ts_dt else "—",
                "updated_at": up_dt.strftime("%Y-%m-%d %H:%M:%S") if up_dt else "—",
                "Atraso (min)": f"{delay:.1f}" if delay else "—",
                "Status": status
            })
        except Exception as e:
            data.append({
                "Robô": key,
                "Último v.ts": str(vts),
                "updated_at": str(upd),
                "Atraso (min)": "—",
                "Status": f"⚠️ Erro: {e}"
            })

    return pd.DataFrame(data)


# ===============================
# UI
# ===============================
st.title("💓 Painel de Heartbeats")
st.caption("Verifica se os robôs estão sendo atualizados corretamente via UptimeRobot.")

col1, col2 = st.columns([3, 1])
with col1:
    st.caption("🕒 Atualiza automaticamente após ping global.")
with col2:
    if st.button("🔁 Forçar ping global e atualizar"):
        if force_global_ping():
            with st.spinner("⏳ Aguardando atualização dos heartbeats..."):
                time.sleep(2)  # pequena espera para o Supabase registrar
            st.rerun()  # recarrega automaticamente a página

st.markdown("---")

df = montar_dataframe()
st.dataframe(df, hide_index=True, use_container_width=True)

st.markdown("---")
st.caption(f"⏱️ Atualizado em: {datetime.datetime.now(_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
st.caption("Legenda: 🟢 <10min | 🟡 <30min | 🔴 ≥30min | ❌ sem heartbeat")
