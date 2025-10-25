# -*- coding: utf-8 -*-
"""
📊 Painel Visual 1Milhão — versão final com fallback automático para arquivos LOSS
Visual idêntico ao anterior, mas agora compatível com robôs que salvam
'visual_state_loss_curto.json' ou 'visual_state_losscurto.json' (etc).
"""

import os
import json
import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional
import streamlit as st
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# ============================
# CONFIGURAÇÕES GERAIS
# ============================
st.set_page_config(page_title="Painel Visual 1Milhão", layout="wide")
# ============================
# LOGO NO TOPO
# ============================
# ============================
# LOGO NO TOPO
# ============================
import base64

logo_path = "Logo-canal-1milhao.png"
if os.path.exists(logo_path):
    with open(logo_path, "rb") as f:
        logo_data = base64.b64encode(f.read()).decode()
    st.markdown(
        f"""
        <div style='text-align: center; margin-top: -20px; margin-bottom: 10px;'>
            <img src='data:image/png;base64,{logo_data}' alt='Logo 1Milhão' style='width:200px;'>
        </div>
        """,
        unsafe_allow_html=True
    )



TZ = ZoneInfo("Europe/Lisbon")
REFRESH_SECONDS = 60
SPARK_MAX_POINTS = 300
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# ============================
# ROBÔS MONITORADOS (VISUAIS)
# ============================
ROBOS = [
    {"key": "curto", "title": "CURTO PRAZO", "emoji": "⚡",
     "files": ["session_data/visual_state_curto.json"]},

    {"key": "loss_curto", "title": "LOSS CURTO", "emoji": "🛑",
     "files": ["session_data/visual_state_losscurto.json",
               "session_data/visual_state_loss_curto.json"]},

    {"key": "curtissimo", "title": "CURTÍSSIMO PRAZO", "emoji": "⚡",
     "files": ["session_data/visual_state_curtissimo.json"]},

    {"key": "loss_curtissimo", "title": "LOSS CURTÍSSIMO", "emoji": "🛑",
     "files": ["session_data/visual_state_losscurtissimo.json",
               "session_data/visual_state_loss_curtissimo.json"]},

    {"key": "clube", "title": "CLUBE", "emoji": "🏛️",
     "files": ["session_data/visual_state_clube.json"]},

    {"key": "loss_clube", "title": "LOSS CLUBE", "emoji": "🏛️🛑",
     "files": ["session_data/visual_state_lossclube.json",
               "session_data/visual_state_loss_clube.json"]},
]

# ============================
# FUNÇÕES AUXILIARES
# ============================
def agora_lx() -> datetime.datetime:
    return datetime.datetime.now(TZ)

def try_load_state(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def summarize_robot_state(state: Dict[str, Any]) -> Dict[str, Any]:
    precos = state.get("precos_historicos", {})
    disparos = state.get("disparos", {})
    total_tickers = len(precos)
    total_disparos = sum(len(v) for v in disparos.values()) if isinstance(disparos, dict) else 0
    tickers = list(precos.keys())
    last_update = None
    for pts in precos.values():
        if pts:
            ts = pts[-1][0]
            try:
                dt = datetime.datetime.fromisoformat(str(ts))
                if not last_update or dt > last_update:
                    last_update = dt
            except Exception:
                continue
    return {"ativos_monitorados": total_tickers, "tickers": tickers,
            "total_disparos": total_disparos, "last_update": last_update}

def build_sparkline(state: Dict[str, Any]) -> Optional[go.Figure]:
    precos = state.get("precos_historicos") or {}
    if not isinstance(precos, dict) or not precos:
        return None
    fig = go.Figure()
    i = 0
    for ticker, pts in precos.items():
        try:
            if not isinstance(pts, list) or len(pts) < 2:
                continue
            xs, ys = [], []
            for p in pts[-SPARK_MAX_POINTS:]:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    ts, price = p
                    try:
                        dt = datetime.datetime.fromisoformat(str(ts))
                    except Exception:
                        continue
                    xs.append(dt)
                    ys.append(float(price))
            if len(xs) < 2:
                continue
            color = PALETTE[i % len(PALETTE)]
            i += 1
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=str(ticker),
                                     line=dict(color=color, width=2)))
        except Exception:
            continue
    if not fig.data:
        return None
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=180,
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
    )
    fig.update_xaxes(title="")
    fig.update_yaxes(title="")
    return fig

def nice_dt(dt: Optional[datetime.datetime]) -> str:
    if not dt:
        return "—"
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

def badge_status_tempo(last_dt: Optional[datetime.datetime]) -> tuple[str, str]:
    if not last_dt:
        return ("🔴 Sem atualização", "red")
    delta_min = (agora_lx() - last_dt).total_seconds() / 60
    if delta_min < 5:
        return ("🟢 Atualizado há poucos minutos", "green")
    elif delta_min < 30:
        return (f"🟡 Último update há {int(delta_min)} min", "yellow")
    else:
        return (f"🔴 Inativo há {int(delta_min)} min", "red")

# ============================
# INTERFACE PRINCIPAL
# ============================
st.title("📊 Painel Visual — 1Milhão")
st.caption(f"Atualiza automaticamente a cada {REFRESH_SECONDS}s")
st_autorefresh(interval=REFRESH_SECONDS * 1000, key="painel-visual-refresh")

colh1, colh2 = st.columns([3, 2])
with colh1:
    st.markdown(f"🕒 Agora: **{agora_lx().strftime('%Y-%m-%d %H:%M:%S %Z')}**")
with colh2:
    st.markdown("📁 Fonte: `session_data/visual_state_*.json` (local)")
st.markdown("---")

# ============================
# RESUMO GERAL
# ============================
total_apps = len(ROBOS)
apps_ok = 0
total_ativos = 0
total_disparos = 0
loaded_states: Dict[str, Dict[str, Any]] = {}

for robo in ROBOS:
    state = None
    for f in robo["files"]:
        state = try_load_state(f)
        if state:
            break
    if state:
        loaded_states[robo["key"]] = state
        s = summarize_robot_state(state)
        total_ativos += s["ativos_monitorados"]
        total_disparos += s["total_disparos"]
        apps_ok += 1

col1, col2, col3 = st.columns(3)
col1.metric("Robôs com dados", f"{apps_ok}/{total_apps}")
col2.metric("Ativos monitorados", total_ativos)
col3.metric("Disparos visuais", total_disparos)
st.markdown("---")

# ============================
# RENDERIZAÇÃO DOS CARDS
# ============================
def render_robot_card(robo: Dict[str, Any], container):
    key = robo["key"]
    title = robo["title"]
    emoji = robo["emoji"]
    with container:
        state = loaded_states.get(key)
        if not state:
            st.markdown(f"### {emoji} {title}")
            st.warning("⛔ Arquivo visual ainda não gerado.")
            st.markdown("---")
            return

        summary = summarize_robot_state(state)
        last_dt = summary["last_update"]
        status_txt, color = badge_status_tempo(last_dt)

        st.markdown(
            f"""
            <div style="border-left: 8px solid {color}; padding-left: 12px; border-radius: 8px;">
            <h3>{emoji} {title}</h3>
            <p>{status_txt} — Última atualização: <b>{nice_dt(last_dt)}</b></p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        c1.metric("Ativos monitorados", summary["ativos_monitorados"])
        c2.metric("Disparos", summary["total_disparos"])

        tickers = summary["tickers"]
        if tickers:
            st.caption("Tickers: " + ", ".join(tickers))

        fig = build_sparkline(state)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Sem histórico suficiente para gráfico.")

        st.markdown("---")

# ============================
# EXIBE OS ROBÔS EM DUAS COLUNAS
# ============================
for i in range(0, len(ROBOS), 2):
    col_left, col_right = st.columns(2)
    render_robot_card(ROBOS[i], col_left)
    if i + 1 < len(ROBOS):
        render_robot_card(ROBOS[i + 1], col_right)

st.markdown("---")
st.caption("© Painel Visual 1Milhão — leitura apenas dos estados locais (sem Supabase).")
