# -*- coding: utf-8 -*-
"""
app.py
Painel Central 1Milhão — Monitor de Robôs (Streamlit)

- Lê os arquivos JSON persistidos por cada robô (APENAS LOCAL)
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
from streamlit_autorefresh import st_autorefresh

import streamlit as st
import requests

# --- Query params (compatível com versões antigas e novas) ---
try:
    q = dict(st.query_params)  # Streamlit ≥ 1.29
except Exception:
    q = st.experimental_get_query_params()  # fallback




# --- Tick leve chamado pelo ?ping=1 ---
import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo

_TZ = _ZoneInfo("Europe/Lisbon")

def run_all_ticks():
    """Executa um ciclo rápido dos robôs: roda cada run_tick() e grava heartbeat individual."""
    now = _dt.datetime.now(_TZ)
    st.session_state["_last_tick"] = now.isoformat()

    for key in PING_MAP.keys():
        try:
            _run_one_tick(key)
            _write_heartbeat_for(key)  # registra heartbeat do robô
        except Exception as e:
            st.session_state.setdefault("_tick_errors", []).append(f"{key}: {e}")


    # 2) (Opcional) chamar ticks específicos se já existir
    # Tente importar funções de tick dos seus módulos/páginas.
    # Se ainda não tiver, deixamos para o próximo passo.
    for mod, fn in [
        ("pages.clube", "run_tick"),
        ("pages.curtissimo", "run_tick"),
        ("pages.curto", "run_tick"),
        ("pages.loss_clube", "run_tick"),
        ("pages.loss_curtissimo", "run_tick"),
        ("pages.loss_curto", "run_tick"),
    ]:
        try:
            _m = __import__(mod, fromlist=[fn])
            if hasattr(_m, fn):
                getattr(_m, fn)()  # chama pages/<mod>.py: def run_tick(): ...
        except Exception as e:
            # não quebrar o ping se algum módulo não tiver tick ainda
            st.session_state.setdefault("_tick_errors", []).append(f"{mod}.{fn}: {e}")
# --- Ping por robô (começando só com 'curto') ---
# --- Ping por robô (começando só com 'curto') ---
PING_MAP = {
    "curto":           ("bots.curto", "run_tick"),
    "curtissimo":      ("bots.curtissimo", "run_tick"),
    "clube":           ("bots.clube", "run_tick"),
    "loss_curto":      ("bots.loss_curto", "run_tick"),
    "loss_curtissimo": ("bots.loss_curtissimo", "run_tick"),
    "loss_clube":      ("bots.loss_clube", "run_tick"),
}

def _run_one_tick(key: str):
    """Roda o tick de um robô específico, se a função existir."""
    mod, fn = PING_MAP[key]
    try:
        m = __import__(mod, fromlist=[fn])
        if hasattr(m, fn):
            return getattr(m, fn)()
    except Exception as e:
        st.session_state.setdefault("_tick_errors", []).append(f"{key}: {e}")
    return None

def _write_heartbeat_for(key: str):
    """Grava heartbeat do robô específico em kv_state_<key> no Supabase."""
    try:
        # Tenta usar secrets específicos do robô (ex: supabase_url_curto)
        url_key = f"supabase_url_{key}"
        key_key = f"supabase_key_{key}"

        if url_key not in st.secrets or key_key not in st.secrets:
            # fallback: usa 'clube' como padrão
            url_key = "supabase_url_clube"
            key_key = "supabase_key_clube"

        supabase_url = st.secrets[url_key]
        supabase_key = st.secrets[key_key]

        now = _dt.datetime.utcnow().isoformat() + "Z"
        url = f"{supabase_url}/rest/v1/kv_state_{key}"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": f"heartbeat_{key}", "v": {"ts": now}}

        requests.post(url, headers=headers, json=payload, timeout=10)

    except Exception as e:
        st.session_state.setdefault("_tick_errors", []).append(f"heartbeat_{key}: {e}")
        pass


def _read_heartbeats(keys: list[str]):
    """
    Lê os heartbeats individuais de cada robô.
    Cada robô tem sua própria tabela: kv_state_<key>.
    Retorna um dicionário {key: datetime_or_None}.
    """
    out = {}
    try:
        for key in keys:
            # Seleciona o par de secrets correspondente (com fallback)
            url_key = f"supabase_url_{key}"
            key_key = f"supabase_key_{key}"

            if url_key not in st.secrets or key_key not in st.secrets:
                url_key = "supabase_url_clube"
                key_key = "supabase_key_clube"

            supabase_url = st.secrets[url_key]
            supabase_key = st.secrets[key_key]

            # Faz leitura da tabela kv_state_<key>
            url = f"{supabase_url}/rest/v1/kv_state_{key}?k=eq.heartbeat_{key}&select=v"
            headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            }
            r = requests.get(url, headers=headers, timeout=10)

            if r.status_code == 200 and r.json():
                ts = r.json()[0]["v"].get("ts")
                if ts:
                    out[key] = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_TZ)
    except Exception as e:
        st.session_state.setdefault("_tick_errors", []).append(f"read_heartbeats: {e}")
        pass

    return out


# --- Heartbeat individual por robô ---
def _write_heartbeat_for(key: str):
    """Grava heartbeat do robô específico em kv_state_clube (k = heartbeat_<key>)."""
    try:
        now = _dt.datetime.utcnow().isoformat() + "Z"
        url = f"{st.secrets['supabase_url_clube']}/rest/v1/kv_state_clube"
        headers = {
            "apikey": st.secrets["supabase_key_clube"],
            "Authorization": f"Bearer {st.secrets['supabase_key_clube']}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": f"heartbeat_{key}", "v": {"ts": now}}
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception:
        pass

def _read_heartbeats(keys):
    """Lê os heartbeats individuais e devolve dict {key: datetime_or_None}."""
    out = {}
    try:
        base = f"{st.secrets['supabase_url_clube']}/rest/v1/kv_state_clube"
        headers = {
            "apikey": st.secrets["supabase_key_clube"],
            "Authorization": f"Bearer {st.secrets['supabase_key_clube']}",
        }
        for k in keys:
            r = requests.get(f"{base}?k=eq.heartbeat_{k}&select=v", headers=headers, timeout=10)
            if r.status_code == 200 and r.json():
                ts = r.json()[0]["v"].get("ts")
                if ts:
                    out[k] = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_TZ)
    except Exception:
        pass
    return out

# Roteamento do ping (depois das definições)
# Roteamento do ping (depois das definições)





# ============================
# CONFIGURAÇÕES GERAIS
# ============================
st.set_page_config(page_title="Painel Central 1Milhão", layout="wide", page_icon="📊")

# --- Barra de pings por robô (chips) ---
_robot_keys = list(PING_MAP.keys())
hb_map = _read_heartbeats(_robot_keys)

def _chip(label, when):
    if not when:
        return f"<span style='margin-right:8px;padding:2px 8px;border-radius:12px;background:#fee2e2;color:#7f1d1d;'>⛔ {label}: sem ping</span>"
    mins = int(((_dt.datetime.now(_TZ) - when).total_seconds()) // 60)
    if mins < 6:
        bg, fg = "#d1fae5", "#065f46"   # verde
    elif mins < 30:
        bg, fg = "#fef3c7", "#78350f"   # amarelo
    else:
        bg, fg = "#fee2e2", "#7f1d1d"   # vermelho
    return f"<span style='margin-right:8px;padding:2px 8px;border-radius:12px;background:{bg};color:{fg};'>{label}: {when.strftime('%H:%M')} ({mins}m)</span>"

chips = "".join(_chip(k, hb_map.get(k)) for k in _robot_keys)
st.markdown(f"<div style='margin:6px 0 12px 0'>{chips}</div>", unsafe_allow_html=True)


# Prova de vida do ping (heartbeat)
hb = _read_heartbeat()
if hb:
    try:
        last_utc = _dt.datetime.fromisoformat(hb.replace("Z", "+00:00"))
        last_local = last_utc.astimezone(_TZ)
        diff_m = int(((_dt.datetime.now(_TZ) - last_local).total_seconds()) // 60)
        st.caption(f"🟢 Último ping: **{last_local.strftime('%Y-%m-%d %H:%M:%S %Z')}** (há {diff_m} min)")
    except Exception:
        st.caption("🟢 Último ping: recebido (erro ao converter horário)")
else:
    st.caption("⚪ Ainda sem heartbeat registrado.")

# Roteamento do ping (depois das definições)
if "ping" in q:
    val = ("" if q["ping"] in ([], None) else str(q["ping"]).lower())
    try:
        if val in ("", "1", "true", "ok", "all", "tudo"):
            run_all_ticks()           # roda todos
            _write_heartbeat()        # heartbeat global (opcional)
            st.write("ok")
        elif val in PING_MAP:          # roda só um robô
            _run_one_tick(val)
            _write_heartbeat_for(val)  # heartbeat desse robô
            st.write(f"ok:{val}")
        else:
            st.write("ok")             # fallback silencioso
    finally:
        st.stop()                      # importantíssimo: não renderiza a UI no ping

# Estilos globais (cards e bolinha flutuante)
st.markdown("""
<style>
body {
    background-color: #050915;
    color: #e5e7eb;
}

.robot-card {
    position: relative;
    background: linear-gradient(145deg, #0c1424 0%, #111827 100%);
    border: 1px solid #1f2937;
    border-radius: 16px;
    padding: 18px 22px;
    margin-bottom: 28px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.35);
    transition: all 0.25s ease-in-out;
}

.robot-card:hover {
    box-shadow: 0 0 15px rgba(16,185,129,0.3);
    border-color: #10b981;
    transform: translateY(-2px);
}

.status-dot {
    position: absolute;
    top: 16px;
    right: 16px;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    box-shadow: 0 0 8px rgba(0,0,0,0.4);
}

/* cores e pulsar */
.status-green {
    background-color: #22c55e;
    animation: pulse-green 1.4s infinite;
}
.status-yellow {
    background-color: #facc15;
    animation: pulse-yellow 2s infinite;
}
.status-red {
    background-color: #ef4444;
    animation: pulse-red 3s infinite;
}

/* Animações */
@keyframes pulse-green {
  0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.6); }
  70% { box-shadow: 0 0 0 12px rgba(34,197,94,0); }
  100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
}
@keyframes pulse-yellow {
  0% { box-shadow: 0 0 0 0 rgba(250,204,21,0.6); }
  70% { box-shadow: 0 0 0 12px rgba(250,204,21,0); }
  100% { box-shadow: 0 0 0 0 rgba(250,204,21,0); }
}
@keyframes pulse-red {
  0% { box-shadow: 0 0 0 0 rgba(239,68,68,0.6); }
  70% { box-shadow: 0 0 0 12px rgba(239,68,68,0); }
  100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
}

/* Ajuste dos títulos e badges */
h3 {
    color: #f9fafb;
}
</style>
""", unsafe_allow_html=True)


# ============================
# CABEÇALHO COM LOGO E TÍTULO (VERSÃO FUNCIONAL)
# ============================

logo_path = "Logo-canal-1milhao.png"  # certifique-se de que o arquivo está na mesma pasta que o app.py

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
        </h1>
        """,
        unsafe_allow_html=True,
    )

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)  # Lisboa
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)    # Lisboa

REFRESH_SECONDS = 60
LOG_PREVIEW_LINES = 5   # linhas de log por robô
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
        "emoji": "⚡",
        "files": [
            "session_data/state_curto.json",
            "state_curto.json"
        ],
        "app_url": None
    },
    {
        "key": "loss_curto",
        "title": "LOSS CURTO",
        "emoji": "🛑",
        "files": [
            "session_data/state_losscurto.json",
            "state_losscurto.json"
        ],
        "app_url": None
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
        "app_url": None
    },
    {
        "key": "loss_curtissimo",
        "title": "LOSS CURTÍSSIMO",
        "emoji": "🛑",
        "files": [
            "session_data/state_losscurtissimo.json",
            "state_losscurtissimo.json"
        ],
        "app_url": None
    },

    # LINHA 3
    {
        "key": "clube",
        "title": "CLUBE",
        "emoji": "🏛️",
        "files": [
            "session_data/state_clube.json",
            "state_clube.json"
        ],
        "app_url": None
    },
    {
        "key": "loss_clube",
        "title": "LOSS CLUBE",
        "emoji": "🏛️🛑",
        "files": [
            "session_data/state_lossclube.json",
            "state_lossclube.json"
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

def badge_status_tempo(last_dt: Optional[datetime.datetime]) -> str:
    """Gera um badge visual de status com base no tempo desde o último update."""
    if not last_dt:
        return format_badge("Sem atualização", color="#991b1b", bg="#fee2e2")
    delta_min = (agora_lx() - last_dt).total_seconds() / 60
    if delta_min < 5:
        return format_badge("🟢 Atualizado há poucos minutos", color="#065f46", bg="#d1fae5")
    elif delta_min < 30:
        return format_badge(f"🟡 Última atualização há {int(delta_min)} min", color="#78350f", bg="#fef3c7")
    else:
        return format_badge(f"🔴 Inativo há {int(delta_min)} min", color="#7f1d1d", bg="#fee2e2")

def status_dot_html(last_dt: Optional[datetime.datetime]) -> str:
    """HTML da bolinha flutuante de status (verde, amarela ou vermelha)."""
    if not last_dt:
        cor = "status-red"
    else:
        delta_min = (agora_lx() - last_dt).total_seconds() / 60
        if delta_min < 5:
            cor = "status-green"
        elif delta_min < 30:
            cor = "status-yellow"
        else:
            cor = "status-red"
    return f"<div class='status-dot {cor}'></div>"

# ============================
# TÍTULO + AUTO-REFRESH
# ============================
st.title("Painel Central")

colh, colr = st.columns([3, 1])
with colh:
    st.caption(
        f"Agora: **{agora_lx().strftime('%Y-%m-%d %H:%M:%S %Z')}** — "
        f"{'🟩 Dentro do pregão' if dentro_pregao(agora_lx()) else '🟥 Fora do pregão'}"
    )
with colr:
    st.caption(f"🔄 Auto-refresh: a cada **{REFRESH_SECONDS}s**")

# 🔁 Auto-refresh real
st_autorefresh(interval=REFRESH_SECONDS * 1000, key="painel-central-refresh")

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

st.markdown("---")

def render_robot_card(robo: Dict[str, Any], container):
    """Renderiza um card individual de robô dentro do container fornecido."""
    key = robo["key"]
    title = robo["title"]
    emoji = robo.get("emoji", "")
    app_url = robo.get("app_url")

    with container:
        # === Card estilizado ===
        st.markdown("<div class='robot-card'>", unsafe_allow_html=True)

        # Título
        st.markdown(f"### {emoji} {title}", unsafe_allow_html=True)

        # Estado e badges
        state = loaded_states.get(key)
        if state is None:
            # bolinha como "sem atualização"
            st.markdown(status_dot_html(None), unsafe_allow_html=True)

            err = errors.get(key)
            if err:
                st.error(err)
            else:
                st.warning("Arquivo de estado ainda não foi criado por este robô.")
            if app_url:
                st.link_button("Abrir app", app_url, type="primary")

            st.markdown("</div>", unsafe_allow_html=True)
            return

        # Bolinha flutuante (usa last_update real)
        last_dt = summarize_robot_state(state)["last_update"]
        st.markdown(status_dot_html(last_dt), unsafe_allow_html=True)

        now_dt = agora_lx()
        status_badge = badge_status_tempo(last_dt)
        badges = (
            f"{badge_pregao(now_dt)} &nbsp;&nbsp; "
            f"{badge_pause(bool(state.get('pausado', False)))} &nbsp;&nbsp; "
            f"{status_badge}"
        )
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

        if app_url:
            st.link_button("Abrir app", app_url, type="primary")


        # Fecha a <div> do card
        st.markdown("</div>", unsafe_allow_html=True)

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
