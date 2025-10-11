# -*- coding: utf-8 -*-
"""
app.py
Painel Central 1Milhão — Monitor de Robôs (Streamlit)
"""

import os
import json
import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ============================
# CONFIGURAÇÕES GERAIS
# ============================
st.set_page_config(page_title="Painel Central 1Milhão", layout="wide", page_icon="📊")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)

REFRESH_SECONDS = 60
LOG_PREVIEW_LINES = 5
SPARK_MAX_POINTS = 300

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# ============================
# MAPEAMENTO DOS ROBÔS
# ============================
ROBOS = [
    {"key": "curto", "title": "CURTO PRAZO", "emoji": "📈",
     "files": ["session_data/state_curto.json", "state_curto.json"],
     "app_url": "https://curtoprazo.streamlit.app"},
    {"key": "curtissimo", "title": "CURTÍSSIMO PRAZO", "emoji": "⚡",
     "files": ["session_data/state_curtissimo.json", "state_curtissimo.json"],
     "app_url": "https://curtissimo.streamlit.app"},
    {"key": "loss_curto", "title": "LOSS CURTO", "emoji": "🛑",
     "files": ["session_data/state_loss_curto.json", "state_loss_curto.json"],
     "app_url": "https://losscurto.streamlit.app"},
    {"key": "loss_curtissimo", "title": "LOSS CURTÍSSIMO", "emoji": "🛑⚡",
     "files": ["session_data/state_loss_curtissimo.json", "state_loss_curtissimo.json"],
     "app_url": "https://losscurtissimo.streamlit.app"},
    {"key": "clube", "title": "CLUBE", "emoji": "🏛️",
     "files": ["session_data/state_clube_compra_venda.json", "state_clube_compra_venda.json"],
     "app_url": None},
    {"key": "loss_clube", "title": "LOSS CLUBE", "emoji": "🏛️🛑",
     "files": ["session_data/state_loss_clube.json", "state_loss_clube.json"],
     "app_url": None},
]

# ============================
# FUNÇÕES AUXILIARES
# ============================
def agora_lx() -> datetime.datetime:
    return datetime.datetime.now(TZ)

def dentro_pregao(dt: datetime.datetime) -> bool:
    t = dt.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def try_load_state(file_candidates: List[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    for path in file_candidates:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data, path, None
        except Exception as e:
            return None, path, f"Erro ao ler {path}: {e}"
    return None, None, None

def format_badge(text: str, color: str = "#1f2937", bg: str = "#e5e7eb"):
    return f"<span style='font-size:12px;padding:2px 8px;border-radius:999px;color:{color};background:{bg};display:inline-block'>{text}</span>"

def get_last_log_lines(state: Dict[str, Any], n: int = 5) -> List[str]:
    lines = state.get("log_monitoramento") or []
    lines = [str(x) for x in lines]
    return lines[-n:][::-1] if lines else []

def build_sparkline(state: Dict[str, Any]) -> Optional[go.Figure]:
    precos = state.get("precos_historicos") or {}
    if not precos:
        return None
    fig = go.Figure()
    i = 0
    for ticker, pts in precos.items():
        xs, ys = [], []
        for p in pts[-SPARK_MAX_POINTS:]:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                ts, price = p
                try:
                    dt = datetime.datetime.fromisoformat(str(ts))
                except Exception:
                    try:
                        dt = datetime.datetime.fromtimestamp(float(ts), tz=TZ)
                    except Exception:
                        continue
                xs.append(dt)
                ys.append(float(price))
        if len(xs) > 1:
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", name=str(ticker),
                line=dict(color=PALETTE[i % len(PALETTE)], width=2)
            ))
            i += 1
    if not fig.data:
        return None
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=160,
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
    )
    return fig

def summarize_robot_state(state: Dict[str, Any]) -> Dict[str, Any]:
    ativos = state.get("ativos") or []
    status = state.get("status") or {}
    historico_alertas = state.get("historico_alertas") or []
    pausado = bool(state.get("pausado", False))
    disparos = state.get("disparos") or {}
    total_disparos = sum(len(v or []) for v in disparos.values()) if isinstance(disparos, dict) else 0
    last_update_map = state.get("ultimo_update_tempo") or {}
    last_update_dt = None
    if isinstance(last_update_map, dict) and last_update_map:
        try:
            last_iso = max((v for v in last_update_map.values() if v), default=None)
            if last_iso:
                last_update_dt = datetime.datetime.fromisoformat(last_iso)
        except Exception:
            pass
    return {
        "ativos_monitorados": len(ativos),
        "tickers": [a.get("ticker") for a in ativos if isinstance(a, dict)],
        "pausado": pausado,
        "status_por_ticker": status,
        "total_alertas": len(historico_alertas),
        "total_disparos": total_disparos,
        "last_update": last_update_dt
    }

def badge_pregao(now_dt: datetime.datetime) -> str:
    return format_badge("Pregão ABERTO", "#065f46", "#d1fae5") if dentro_pregao(now_dt) else format_badge("Pregão FECHADO", "#7c2d12", "#ffedd5")

def badge_pause(pausado: bool) -> str:
    return format_badge("PAUSADO", "#7c2d12", "#fee2e2") if pausado else format_badge("ATIVO", "#065f46", "#dcfce7")

def nice_dt(dt: Optional[datetime.datetime]) -> str:
    if not dt:
        return "—"
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

# ============================
# TÍTULO + AUTO-REFRESH
# ============================
st.title("📊 Painel Central — Robôs 1Milhão")

st.caption(
    f"Agora: **{agora_lx().strftime('%Y-%m-%d %H:%M:%S %Z')}** — "
    f"{'🟩 Dentro do pregão' if dentro_pregao(agora_lx()) else '🟥 Fora do pregão'} | "
    f"🔄 Auto-refresh: {REFRESH_SECONDS}s"
)

st.info("Dica: mantenha os apps individuais rodando para que os JSONs estejam sempre atualizados.")

# ============================
# CARREGAR ESTADOS
# ============================
loaded_states, resolved_paths, errors = {}, {}, {}
apps_ok = total_ativos = total_disparos = total_alertas = 0
for robo in ROBOS:
    data, path, err = try_load_state(robo["files"])
    if data is not None:
        loaded_states[robo["key"]] = data
        resolved_paths[robo["key"]] = path or "—"
        s = summarize_robot_state(data)
        total_ativos += s["ativos_monitorados"]
        total_disparos += s["total_disparos"]
        total_alertas += s["total_alertas"]
        apps_ok += 1
    elif err:
        errors[robo["key"]] = err

col1, col2, col3, col4 = st.columns(4)
col1.metric("Robôs ativos", f"{apps_ok}/{len(ROBOS)}")
col2.metric("Ativos monitorados", total_ativos)
col3.metric("Disparos", total_disparos)
col4.metric("Alertas", total_alertas)

st.markdown("---")

# ============================
# FUNÇÃO DE CARD
# ============================
def render_robot_card(robo: Dict[str, Any], border_color: str):
    """Renderiza um card completo com moldura colorida."""
    with st.container():
        st.markdown(
            f"""
            <div style='border:2px solid {border_color};
                        border-radius:16px;
                        padding:22px;
                        margin-bottom:25px;
                        box-shadow:0 0 12px {border_color}40;'>
            """, unsafe_allow_html=True)

        key = robo["key"]
        title = robo["title"]
        emoji = robo.get("emoji", "")
        app_url = robo.get("app_url")
        st.markdown(f"### {emoji} {title}")

        state = loaded_states.get(key)
        if state is None:
            err = errors.get(key)
            if err:
                st.error(err)
            else:
                st.warning("Arquivo de estado ainda não foi criado por este robô.")
            if app_url:
                st.link_button("Abrir app", app_url, type="primary")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        now_dt = agora_lx()
        st.markdown(
            f"{badge_pregao(now_dt)} &nbsp;&nbsp; {badge_pause(bool(state.get('pausado', False)))}",
            unsafe_allow_html=True)

        summary = summarize_robot_state(state)
        c1, c2, c3 = st.columns(3)
        c1.metric("Ativos", summary["ativos_monitorados"])
        c2.metric("Disparos", summary["total_disparos"])
        c3.metric("Alertas", summary["total_alertas"])

        tickers = summary["tickers"] or []
        st.caption("Tickers: " + ", ".join(tickers) if tickers else "Tickers: —")

        fig = build_sparkline(state)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Sem histórico suficiente para gráfico.")

        st.markdown("**Log recente:**")
        for ln in get_last_log_lines(state, LOG_PREVIEW_LINES) or ["Sem entradas de log."]:
            st.code(ln, language="text")

        p1, p2 = st.columns(2)
        p1.caption(f"Último update interno: **{nice_dt(summary['last_update'])}**")
        p2.caption(f"Fonte de estado: `{resolved_paths.get(key, '—')}`")

        bt_col1, bt_col2 = st.columns([1, 3])
        if app_url:
            bt_col1.link_button("Abrir app", app_url, type="primary")
        bt_col2.button("Forçar refresh", key=f"refresh_{key}")

        st.markdown("</div>", unsafe_allow_html=True)

# ============================
# GRID FINAL DE CARDS
# ============================
for i in range(0, len(ROBOS), 2):
    cols = st.columns(2)
    with cols[0]:
        render_robot_card(ROBOS[i], "#10B981")
    if i + 1 < len(ROBOS):
        with cols[1]:
            render_robot_card(ROBOS[i + 1], "#EF4444")
    st.markdown("---")

st.caption("© Painel Central 1Milhão — consolidado dos robôs.")









