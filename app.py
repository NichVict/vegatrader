# -*- coding: utf-8 -*-
"""
app.py
Painel Central 1Milhão — Monitor de Robôs (Streamlit)

- Lê os arquivos JSON persistidos por cada robô
- Mostra status consolidado + resumo por robô
- Auto-refresh a cada 60s
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

# ============================
# CABEÇALHO COM LOGO E TÍTULO
# ============================

# ============================
# CABEÇALHO COM LOGO E TÍTULO (VERSÃO FUNCIONAL)
# ============================

logo_path = "logo_vega_gpt_transp.png"  # certifique-se de que o arquivo está na mesma pasta que o app.py

header_col1, header_col2 = st.columns([1, 6])

with header_col1:
    try:
        st.image(logo_path, width=120)
    except Exception:
        st.warning("⚠️ Logo não encontrado: verifique o nome do arquivo e a pasta.")

with header_col2:
    st.markdown(
        """
        <h1 style="color:#10B981; font-size: 2.2em; margin-bottom:0;">
            Painel Central
        </h1>
        """,
        unsafe_allow_html=True,
    )


TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)  # Lisboa
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)  # Lisboa

REFRESH_SECONDS = 60
LOG_PREVIEW_LINES = 5  # linhas de log por robô
SPARK_MAX_POINTS = 300  # limita pontos da sparkline por robô

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# ============================
# MAPEAMENTO DOS ROBÔS (ORDENADOS E ALINHADOS)
# ============================
ROBOS = [
    # LINHA 1
    {
        "key": "curto",
        "title": "CURTO PRAZO",
        "emoji": "📈",
        "files": [
            "session_data/state_curto.json",
            "state_curto.json"
        ],
        "app_url": "https://curtoprazo.streamlit.app"
    },
    {
        "key": "loss_curto",
        "title": "LOSS CURTO",
        "emoji": "🛑",
        "files": [
            "session_data/state_loss_curto.json",
            "state_loss_curto.json"
        ],
        "app_url": "https://losscurto.streamlit.app"
    },

    # LINHA 2
    {
        "key": "curtissimo",
        "title": "CURTÍSSIMO PRAZO",
        "emoji": "⚡",
        "files": [
            "session_data/state_curtissimo.json",
            "state_curtissimo.json"
        ],
        "app_url": "https://curtissimo.streamlit.app"
    },
    {
        "key": "loss_curtissimo",
        "title": "LOSS CURTÍSSIMO",
        "emoji": "🛑⚡",
        "files": [
            "session_data/state_loss_curtissimo.json",
            "session_state_losscurtissimo.json",
            "state_losscurtissimo.json"
        ],
        "app_url": "https://losscurtissimo.streamlit.app"
    },

    # LINHA 3
    {
        "key": "clube",
        "title": "CLUBE",
        "emoji": "🏛️",
        "files": [
            "session_data/state_clube_compra_venda.json",
            "state_clube_compra_venda.json"
        ],
        "app_url": None
    },
    {
        "key": "loss_clube",
        "title": "LOSS CLUBE",
        "emoji": "🏛️🛑",
        "files": [
            "session_data/state_loss_clube.json",
            "state_loss_clube.json"
        ],
        "app_url": None
    },
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
    if not lines:
        return []
    return lines[-n:][::-1]


def build_sparkline(state: Dict[str, Any]) -> Optional[go.Figure]:
    precos = state.get("precos_historicos") or {}
    if not isinstance(precos, dict) or not precos:
        return None

    fig = go.Figure()
    color_map = {}
    i = 0
    added_any = False

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
                        try:
                            dt = datetime.datetime.fromtimestamp(float(ts), tz=TZ)
                        except Exception:
                            continue
                    xs.append(dt)
                    ys.append(float(price))
            if len(xs) < 2:
                continue

            if ticker not in color_map:
                color_map[ticker] = PALETTE[i % len(PALETTE)]
                i += 1

            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", name=str(ticker),
                line=dict(color=color_map[ticker], width=2)
            ))
            added_any = True
        except Exception:
            continue

    if not added_any:
        return None

    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=160,
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
    )
    fig.update_xaxes(title="")
    fig.update_yaxes(title="")
    return fig


def summarize_robot_state(state: Dict[str, Any]) -> Dict[str, Any]:
    ativos = state.get("ativos") or []
    status = state.get("status") or {}
    historico_alertas = state.get("historico_alertas") or []
    pausado = bool(state.get("pausado", False))
    disparos = state.get("disparos") or {}
    total_disparos = sum(len(v or []) for v in disparos.values()) if isinstance(disparos, dict) else 0
    ultimo_update_map = state.get("ultimo_update_tempo") or {}
    last_update_dt = None
    if isinstance(ultimo_update_map, dict) and ultimo_update_map:
        try:
            last_iso = max((v for v in ultimo_update_map.values() if v), default=None)
            if last_iso:
                last_update_dt = datetime.datetime.fromisoformat(last_iso)
        except Exception:
            last_update_dt = None

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
    if dentro_pregao(now_dt):
        return format_badge("Pregão ABERTO", color="#065f46", bg="#d1fae5")
    else:
        return format_badge("Pregão FECHADO", color="#7c2d12", bg="#ffedd5")


def badge_pause(pausado: bool) -> str:
    if pausado:
        return format_badge("PAUSADO", color="#7c2d12", bg="#fee2e2")
    else:
        return format_badge("ATIVO", color="#065f46", bg="#dcfce7")


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

colh, colr = st.columns([3, 1])
with colh:
    st.caption(
        f"Agora: **{agora_lx().strftime('%Y-%m-%d %H:%M:%S %Z')}** — "
        f"{'🟩 Dentro do pregão' if dentro_pregao(agora_lx()) else '🟥 Fora do pregão'}"
    )
with colr:
    st.caption(f"🔄 Auto-refresh: a cada **{REFRESH_SECONDS}s**")

st.info("Dica: mantenha os apps individuais rodando (ou use keep-alive lá) para que os JSONs estejam sempre atualizados.")

# ============================
# CARDS RESUMO (TOPO)
# ============================
total_apps = len(ROBOS)
apps_ok = 0
total_ativos = 0
total_disparos = 0
total_alertas = 0

loaded_states: Dict[str, Dict[str, Any]] = {}
resolved_paths: Dict[str, str] = {}
errors: Dict[str, str] = {}

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
col1.metric("Robôs ativos (com estado)", f"{apps_ok}/{total_apps}")
col2.metric("Ativos monitorados (total)", total_ativos)
col3.metric("Disparos acumulados", total_disparos)
col4.metric("Alertas (histórico)", total_alertas)

# ============================
# GRID DE CARDS POR ROBÔ (ALINHADO)
# ============================
st.markdown("---")

left_col, right_col = st.columns(2)


# ============================
# GRID DE CARDS POR ROBÔ (ALINHADO)
# ============================
st.markdown("---")


def render_robot_card(robo: Dict[str, Any], container):
    """Renderiza um card individual de robô dentro do container fornecido."""
    key = robo["key"]
    title = robo["title"]
    emoji = robo.get("emoji", "")
    app_url = robo.get("app_url")

    with container:
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
            return  # sem st.markdown("---") aqui para manter altura constante

        now_dt = agora_lx()
        badges = f"{badge_pregao(now_dt)} &nbsp;&nbsp; {badge_pause(bool(state.get('pausado', False)))}"
        st.markdown(badges, unsafe_allow_html=True)

        summary = summarize_robot_state(state)

        c1, c2, c3 = st.columns(3)
        c1.metric("Ativos monitorados", summary["ativos_monitorados"])
        c2.metric("Disparos (sessão)", summary["total_disparos"])
        c3.metric("Alertas (histórico)", summary["total_alertas"])

        tickers = summary["tickers"] or []
        if tickers:
            st.caption("Tickers: " + ", ".join([str(t) for t in tickers]))
        else:
            st.caption("Tickers: —")

        fig = build_sparkline(state)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Sem histórico suficiente para gráfico.")

        st.markdown("**Log recente:**")
        lines = get_last_log_lines(state, LOG_PREVIEW_LINES)
        if lines:
            for ln in lines:
                st.code(ln, language="text")
        else:
            st.caption("Sem entradas de log ainda.")

        p1, p2 = st.columns(2)
        with p1:
            st.caption(f"Último update interno: **{nice_dt(summary['last_update'])}**")
        with p2:
            path_used = resolved_paths.get(key, "—")
            st.caption(f"Fonte de estado: `{path_used}`")

        bt_col1, bt_col2 = st.columns([1, 3])
        if app_url:
            bt_col1.link_button("Abrir app", app_url, type="primary")
        bt_col2.button("Forçar refresh", key=f"refresh_{key}")


# ============================
# RENDERIZAÇÃO EM PARES (ESQ ↔ DIR)
# ============================
for i in range(0, len(ROBOS), 2):
    with st.container():
        col_left, col_right = st.columns(2)
        render_robot_card(ROBOS[i], col_left)
        if i + 1 < len(ROBOS):
            render_robot_card(ROBOS[i + 1], col_right)
    # divisória entre linhas
    st.markdown("---")


# ============================
# RODAPÉ
# ============================
st.caption(
    "© Painel Central 1Milhão — consolidado dos robôs. "
    "Mantenha cada app em execução para dados atualizados."
)







