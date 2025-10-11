# -*- coding: utf-8 -*-
"""
app.py
Painel Central 1Milhão — Monitor de Robôs (Streamlit)

- Lê os arquivos JSON persistidos por cada robô
- Lê o estado do CURTÍSSIMO via Supabase
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

# Import Supabase client
from supabase import create_client

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
    {
        "key": "curto",
        "title": "CURTO PRAZO",
        "emoji": "📈",
        "files": [
            "session_data/state_curto.json",
            "state_curto.json"
        ],
        "app_url": "https://curtoprazo.streamlit.app",
        "source": "file"
    },
    {
        "key": "curtissimo",
        "title": "CURTÍSSIMO PRAZO",
        "emoji": "⚡",
        "files": [],
        "app_url": "https://curtissimo.streamlit.app",
        "source": "supabase"  # ⚙️ busca no banco
    },
    {
        "key": "loss_curto",
        "title": "LOSS CURTO",
        "emoji": "🛑",
        "files": [
            "session_data/state_loss_curto.json",
            "state_loss_curto.json"
        ],
        "app_url": "https://losscurto.streamlit.app",
        "source": "file"
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
        "app_url": "https://losscurtissimo.streamlit.app",
        "source": "file"
    },
    {
        "key": "clube",
        "title": "CLUBE",
        "emoji": "🏛️",
        "files": [
            "session_data/state_clube_compra_venda.json",
            "state_clube_compra_venda.json"
        ],
        "app_url": "https://clube.streamlit.app",
        "source": "file"
    },
    {
        "key": "loss_clube",
        "title": "LOSS CLUBE",
        "emoji": "🏛️🛑",
        "files": [
            "session_data/state_loss_clube.json",
            "state_loss_clube.json"
        ],
        "app_url": "https://lossclube.streamlit.app",
        "source": "file"
    },
]

# ============================
# SUPABASE CONFIG
# ============================
SUPABASE_URL = st.secrets.get("supabase_url", "")
SUPABASE_KEY = st.secrets.get("supabase_key", "")
TABLE_NAME = "kv_state_curtissimo"
STATE_KEY = "curtissimo_przo_v1"

def try_load_state_supabase() -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Lê o estado do CURTÍSSIMO diretamente do Supabase."""
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Credenciais Supabase ausentes em st.secrets")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        res = supabase.table(TABLE_NAME).select("*").eq("key", STATE_KEY).execute()
        if not res.data:
            return None, "supabase", "Nenhum estado encontrado no Supabase"
        record = res.data[0]
        value = record.get("value")
        if isinstance(value, str):
            state = json.loads(value)
        elif isinstance(value, dict):
            state = value
        else:
            return None, "supabase", "Formato inesperado no campo 'value'"
        return state, "supabase", None
    except Exception as e:
        return None, "supabase", f"Erro Supabase: {e}"

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
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=str(ticker),
                                     line=dict(color=color_map[ticker], width=2)))
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
# CABEÇALHO
# ============================
st.title("📊 Painel Central — Robôs 1Milhão")

colh, colr = st.columns([3, 1])
with colh:
    st.caption(f"Agora: **{agora_lx().strftime('%Y-%m-%d %H:%M:%S %Z')}** — "
               f"{'🟩 Dentro do pregão' if dentro_pregao(agora_lx()) else '🟥 Fora do pregão'}")
with colr:
    st.caption(f"🔄 Auto-refresh: a cada **{REFRESH_SECONDS}s**")

st.info("Dica: mantenha os apps individuais rodando (ou use keep-alive lá) para que os dados estejam sempre atualizados.")

# ============================
# LEITURA DOS ESTADOS
# ============================
loaded_states = {}
resolved_paths = {}
errors = {}
total_apps = len(ROBOS)
apps_ok = total_ativos = total_disparos = total_alertas = 0

for robo in ROBOS:
    if robo.get("source") == "supabase":
        data, path, err = try_load_state_supabase()
    else:
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
col2.metric("Ativos monitorados", total_ativos)
col3.metric("Disparos acumulados", total_disparos)
col4.metric("Alertas (histórico)", total_alertas)

# ============================
# RENDERIZAÇÃO DOS CARDS
# ============================
st.markdown("---")
grid_cols = st.columns(2)

def render_robot_card(robo, container):
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
                st.warning("Estado não encontrado ainda.")
            if app_url:
                st.link_button("Abrir app", app_url, type="primary")
            st.markdown("---")
            return

        badges = f"{badge_pregao(agora_lx())} &nbsp;&nbsp; {badge_pause(state.get('pausado', False))}"
        st.markdown(badges, unsafe_allow_html=True)

        summary = summarize_robot_state(state)
        c1, c2, c3 = st.columns(3)
        c1.metric("Ativos monitorados", summary["ativos_monitorados"])
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
        lines = get_last_log_lines(state, LOG_PREVIEW_LINES)
        if lines:
            for ln in lines:
                st.code(ln, language="text")
        else:
            st.caption("Sem entradas de log.")

        p1, p2 = st.columns(2)
        p1.caption(f"Último update: **{nice_dt(summary['last_update'])}**")
        p2.caption(f"Fonte: `{resolved_paths.get(key, 'Supabase' if robo.get('source')=='supabase' else '—')}`")

        b1, b2 = st.columns([1, 3])
        if app_url:
            b1.link_button("Abrir app", app_url, type="primary")
        b2.button("Forçar refresh", key=f"refresh_{key}")

        st.markdown("---")

left, right = grid_cols
for i, robo in enumerate(ROBOS):
    render_robot_card(robo, left if i % 2 == 0 else right)

# ============================
# RODAPÉ
# ============================
st.caption("© Painel Central 1Milhão — consolidado dos robôs. Todos os dados em tempo real.")





