# -*- coding: utf-8 -*-
"""
Painel Central 1Milhão — Monitor de Robôs (Streamlit)

- Lê arquivos JSON locais (session_data)
- Mostra status consolidado dos robôs
- Faz ping/heartbeat individual e global via Supabase
- Auto-refresh a cada 60 s
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
import requests
from importlib import import_module

# ============================
# CONFIGURAÇÕES BÁSICAS
# ============================

st.set_page_config(page_title="Painel Central 1Milhão", layout="wide", page_icon="📊")

try:
    q = dict(st.query_params)  # Streamlit ≥ 1.29
except Exception:
    q = st.experimental_get_query_params()

_TZ = ZoneInfo("Europe/Lisbon")

# ============================
# MAPA DOS ROBÔS (módulo e função)
# ============================

PING_MAP = {
    "curto":           ("bots.curto", "run_tick"),
    "curtissimo":      ("bots.curtissimo", "run_tick"),
    "clube":           ("bots.clube", "run_tick"),
    "loss_curto":      ("bots.loss_curto", "run_tick"),
    "loss_curtissimo": ("bots.loss_curtissimo", "run_tick"),
    "loss_clube":      ("bots.loss_clube", "run_tick"),
}

# Tabelas REAIS no Supabase
TABLE_MAP = {
    "curto":           "kv_state_curto",
    "curtissimo":      "kv_state_curtissimo",
    "clube":           "kv_state_clube",
    "loss_curto":      "kv_state_losscurto",
    "loss_curtissimo": "kv_state_losscurtissimo",
    "loss_clube":      "kv_state_lossclube",
}

# Chave 'k' usada no heartbeat (nos loss COM underscore após 'loss_')
HBKEY_MAP = {
    "curto":           "heartbeat_curto",
    "curtissimo":      "heartbeat_curtissimo",
    "clube":           "heartbeat_clube",
    "loss_curto":      "heartbeat_loss_curto",
    "loss_curtissimo": "heartbeat_loss_curtissimo",
    "loss_clube":      "heartbeat_loss_clube",
}

# Secrets por robô (fallback no do clube)
SECRETS_URL_KEY = {
    "curto":           "supabase_url_curto",
    "curtissimo":      "supabase_url_curtissimo",
    "clube":           "supabase_url_clube",
    "loss_curto":      "supabase_url_loss_curto",
    "loss_curtissimo": "supabase_url_loss_curtissimo",
    "loss_clube":      "supabase_url_loss_clube",
}
SECRETS_API_KEY = {
    "curto":           "supabase_key_curto",
    "curtissimo":      "supabase_key_curtissimo",
    "clube":           "supabase_key_clube",
    "loss_curto":      "supabase_key_loss_curto",
    "loss_curtissimo": "supabase_key_loss_curtissimo",
    "loss_clube":      "supabase_key_loss_clube",
}

def _get_supabase_creds(key: str) -> tuple[str, str]:
    url_name = SECRETS_URL_KEY.get(key, "supabase_url_clube")
    api_name = SECRETS_API_KEY.get(key, "supabase_key_clube")
    if url_name not in st.secrets or api_name not in st.secrets:
        url_name = "supabase_url_clube"
        api_name = "supabase_key_clube"
    return st.secrets[url_name], st.secrets[api_name]

# ============================
# HEARTBEATS (individual e global)
# ============================

def _write_heartbeat_for(key: str) -> bool:
    """Grava heartbeat individual por robô."""
    try:
        supabase_url, supabase_key = _get_supabase_creds(key)
        table_name = TABLE_MAP.get(key, "kv_state_clube")
        hb_key = HBKEY_MAP.get(key, f"heartbeat_{key}")
        now = datetime.datetime.utcnow().isoformat() + "Z"

        url = f"{supabase_url}/rest/v1/{table_name}"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": hb_key, "v": {"ts": now}}
        r = requests.post(url, headers=headers, json=payload, timeout=12)

        if r.status_code not in (200, 201):
            st.session_state.setdefault("_tick_errors", []).append(
                f"write_hb[{key}] HTTP {r.status_code}: {r.text}"
            )
            return False
        return True
    except Exception as e:
        st.session_state.setdefault("_tick_errors", []).append(f"write_hb[{key}]: {e}")
        return False

def _write_heartbeat_global() -> None:
    """Grava heartbeat global (no kv_state_clube)."""
    try:
        supabase_url, supabase_key = _get_supabase_creds("clube")
        url = f"{supabase_url}/rest/v1/kv_state_clube"
        now = datetime.datetime.utcnow().isoformat() + "Z"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": "heartbeat_global", "v": {"ts": now}}
        requests.post(url, headers=headers, json=payload, timeout=12)
    except Exception as e:
        st.session_state.setdefault("_tick_errors", []).append(f"write_hb_global: {e}")

def _read_heartbeat_global() -> Optional[str]:
    try:
        supabase_url, supabase_key = _get_supabase_creds("clube")
        url = f"{supabase_url}/rest/v1/kv_state_clube?k=eq.heartbeat_global&select=v"
        headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200 and r.json():
            return r.json()[0]["v"].get("ts")
    except Exception:
        return None
    return None

def _read_heartbeats(keys: list[str]) -> Dict[str, datetime.datetime]:
    """Lê heartbeats individuais diretamente do Supabase."""
    out: Dict[str, datetime.datetime] = {}
    for key in keys:
        try:
            supabase_url, supabase_key = _get_supabase_creds(key)
            table_name = TABLE_MAP.get(key, "kv_state_clube")
            hb_key = HBKEY_MAP.get(key, f"heartbeat_{key}")
            url = f"{supabase_url}/rest/v1/{table_name}?k=eq.{hb_key}&select=v"
            headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code == 200 and r.json():
                ts = r.json()[0]["v"].get("ts")
                if ts:
                    out[key] = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_TZ)
        except Exception as e:
            st.session_state.setdefault("_tick_errors", []).append(f"read_hb[{key}]: {e}")
    return out

# ============================
# EXECUÇÃO DOS ROBÔS
# ============================

def _run_one_tick(key: str):
    """Importa e roda o run_tick() do robô."""
    try:
        mod, fn = PING_MAP[key]
        m = import_module(mod)
        if hasattr(m, fn):
            return getattr(m, fn)()
    except Exception as e:
        st.session_state.setdefault("_tick_errors", []).append(f"run_tick[{key}]: {e}")
    return None

# =========================================
# 🔁 HANDLER DE ?ping=  (UptimeRobot / cron)
# =========================================
if "ping" in q:
    val = ("" if q["ping"] in ([], None) else str(q["ping"]).lower())
    try:
        if val in ("", "1", "true", "ok", "all", "tudo"):
            # roda todos e grava HB de cada
            for key in PING_MAP:
                _run_one_tick(key)
                _write_heartbeat_for(key)
            _write_heartbeat_global()
            st.write("ok")
        elif val in PING_MAP:
            _run_one_tick(val)
            _write_heartbeat_for(val)
            _write_heartbeat_global()
            st.write(f"ok:{val}")
        else:
            st.write("ok")  # silencioso para valores aleatórios
    finally:
        st.stop()  # não renderiza UI para chamadas de ping

# ============================
# UI — BARRA DE PINGS (CHIPS)
# ============================

st.caption(f"🔄 Atualizado em: {datetime.datetime.now(_TZ).strftime('%H:%M:%S')}")

_robot_keys = list(PING_MAP.keys())
hb_map = _read_heartbeats(_robot_keys)

def _chip(label, when, reference_time=None):
    if not when:
        return f"<span style='margin-right:8px;padding:2px 8px;border-radius:12px;background:#fee2e2;color:#7f1d1d;'>⛔ {label}: sem ping</span>"
    now = datetime.datetime.now(_TZ)
    mins = int((now - when).total_seconds() // 60)
    delta_ref = abs((reference_time - when).total_seconds()) / 60 if reference_time else 0
    if delta_ref <= 1 or mins < 6:
        bg, fg = "#d1fae5", "#065f46"  # verde
    elif mins < 30:
        bg, fg = "#fef3c7", "#78350f"  # amarelo
    else:
        bg, fg = "#fee2e2", "#7f1d1d"  # vermelho
    return f"<span style='margin-right:8px;padding:2px 8px;border-radius:12px;background:{bg};color:{fg};'>{label}: {when.strftime('%H:%M')} ({mins}m)</span>"

ref_time = max(hb_map.values()) if hb_map else None
chips = "".join(_chip(k, hb_map.get(k), ref_time) for k in _robot_keys)
st.markdown(f"<div style='margin:6px 0 12px 0'>{chips}</div>", unsafe_allow_html=True)

# ============================
# HEARTBEAT GLOBAL
# ============================

hb = _read_heartbeat_global()
if hb:
    try:
        last_utc = datetime.datetime.fromisoformat(hb.replace("Z", "+00:00"))
        last_local = last_utc.astimezone(_TZ)
        diff_m = int((datetime.datetime.now(_TZ) - last_local).total_seconds() // 60)
        color = "🟢" if diff_m < 6 else ("🟡" if diff_m < 20 else "⚪")
        st.caption(f"{color} Último ping global: **{last_local.strftime('%Y-%m-%d %H:%M:%S %Z')}** (há {diff_m} min)")
    except Exception:
        st.caption("🟢 Último ping global: recebido (erro ao converter horário)")
else:
    st.caption("⚪ Ainda sem heartbeat global registrado.")

# ============================
# ESTILO
# ============================

st.markdown("""
<style>
body { background-color: #050915; color: #e5e7eb; }
.robot-card {
  position: relative; background: linear-gradient(145deg, #0c1424 0%, #111827 100%);
  border: 1px solid #1f2937; border-radius: 16px; padding: 18px 22px; margin-bottom: 28px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.35); transition: all 0.25s ease-in-out;
}
.robot-card:hover { box-shadow: 0 0 15px rgba(16,185,129,0.3); border-color: #10b981; transform: translateY(-2px); }
.status-dot { position: absolute; top: 16px; right: 16px; width: 14px; height: 14px; border-radius: 50%; box-shadow: 0 0 8px rgba(0,0,0,0.4); }
.status-green { background-color: #22c55e; animation: pulse-green 1.4s infinite; }
.status-yellow { background-color: #facc15; animation: pulse-yellow 2s infinite; }
.status-red { background-color: #ef4444; animation: pulse-red 3s infinite; }
@keyframes pulse-green {0%{box-shadow:0 0 0 0 rgba(34,197,94,0.6);}70%{box-shadow:0 0 0 12px rgba(34,197,94,0);}100%{box-shadow:0 0 0 0 rgba(34,197,94,0);}}
@keyframes pulse-yellow{0%{box-shadow:0 0 0 0 rgba(250,204,21,0.6);}70%{box-shadow:0 0 0 12px rgba(250,204,21,0);}100%{box-shadow:0 0 0 0 rgba(250,204,21,0);}}
@keyframes pulse-red{0%{box-shadow:0 0 0 0 rgba(239,68,68,0.6);}70%{box-shadow:0 0 0 12px rgba(239,68,68,0);}100%{box-shadow:0 0 0 0 rgba(239,68,68,0);}}
h3 { color: #f9fafb; }
</style>
""", unsafe_allow_html=True)

# ============================
# CABEÇALHO
# ============================

logo_path = "Logo-canal-1milhao.png"
c1, c2 = st.columns([1, 6])
with c1:
    try:
        st.image(logo_path, width=120)
    except Exception:
        st.warning("⚠️ Logo não encontrado: verifique o nome do arquivo e a pasta.")
with c2:
    st.markdown("<h1 style='color:#10B981; font-size: 2.2em; margin-bottom:0;'></h1>", unsafe_allow_html=True)

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)

REFRESH_SECONDS = 60
LOG_PREVIEW_LINES = 5
SPARK_MAX_POINTS = 300

PALETTE = ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"]

# ============================
# ROBÔS
# ============================

ROBOS = [
    {"key": "curto",           "title": "CURTO PRAZO",       "emoji": "⚡",
     "files": ["session_data/state_curto.json","state_curto.json"], "app_url": None},
    {"key": "loss_curto",      "title": "LOSS CURTO",        "emoji": "🛑",
     "files": ["session_data/state_losscurto.json","state_losscurto.json"], "app_url": None},
    {"key": "curtissimo",      "title": "CURTÍSSIMO PRAZO",  "emoji": "⚡",
     "files": ["session_data/state_curtissimo.json","state_curtissimo.json"], "app_url": None},
    {"key": "loss_curtissimo", "title": "LOSS CURTÍSSIMO",   "emoji": "🛑",
     "files": ["session_data/state_losscurtissimo.json","state_losscurtissimo.json"], "app_url": None},
    {"key": "clube",           "title": "CLUBE",             "emoji": "🏛️",
     "files": ["session_data/state_clube.json","state_clube.json"], "app_url": None},
    {"key": "loss_clube",      "title": "LOSS CLUBE",        "emoji": "🏛️🛑",
     "files": ["session_data/state_lossclube.json","state_lossclube.json"], "app_url": None},
]

# ============================
# FUNÇÕES AUXILIARES (UI)
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
    if not lines: return []
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
                    xs.append(dt); ys.append(float(price))
            if len(xs) < 2: continue
            if ticker not in color_map:
                color_map[ticker] = PALETTE[i % len(PALETTE)]; i += 1
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=str(ticker),
                                     line=dict(color=color_map[ticker], width=2)))
            added_any = True
        except Exception:
            continue
    if not added_any:
        return None
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=160,
                      template="plotly_dark",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    fig.update_xaxes(title=""); fig.update_yaxes(title=""); return fig

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
    return format_badge("Pregão ABERTO", "#065f46", "#d1fae5") if dentro_pregao(now_dt) else format_badge("Pregão FECHADO", "#7c2d12", "#ffedd5")

def badge_pause(pausado: bool) -> str:
    return format_badge("ATIVO", "#065f46", "#dcfce7") if not pausado else format_badge("PAUSADO", "#7c2d12", "#fee2e2")

def nice_dt(dt: Optional[datetime.datetime]) -> str:
    if not dt: return "—"
    if not dt.tzinfo: dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

def badge_status_tempo(last_dt: Optional[datetime.datetime]) -> str:
    if not last_dt:
        return format_badge("Sem atualização", "#991b1b", "#fee2e2")
    delta_min = (agora_lx() - last_dt).total_seconds() / 60
    if delta_min < 5:
        return format_badge("🟢 Atualizado há poucos minutos", "#065f46", "#d1fae5")
    elif delta_min < 30:
        return format_badge(f"🟡 Última atualização há {int(delta_min)} min", "#78350f", "#fef3c7")
    else:
        return format_badge(f"🔴 Inativo há {int(delta_min)} min", "#7f1d1d", "#fee2e2")

def status_dot_html(last_dt: Optional[datetime.datetime]) -> str:
    if not last_dt: cor = "status-red"
    else:
        delta_min = (agora_lx() - last_dt).total_seconds() / 60
        cor = "status-green" if delta_min < 5 else ("status-yellow" if delta_min < 30 else "status-red")
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
    st.caption(f"🔄 Auto-refresh: a cada **60s**")

st_autorefresh(interval=60 * 1000, key="painel-central-refresh")

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

c1, c2, c3, c4 = st.columns(4)
c1.metric("Robôs ativos (com estado)", f"{apps_ok}/{total_apps}")
c2.metric("Ativos monitorados (total)", total_ativos)
c3.metric("Disparos acumulados", total_disparos)
c4.metric("Alertas (histórico)", total_alertas)

# ============================
# GRID DE CARDS POR ROBÔ
# ============================

st.markdown("---")
left_col, right_col = st.columns(2)
st.markdown("---")

def render_robot_card(robo: Dict[str, Any], container):
    key = robo["key"]; title = robo["title"]; emoji = robo.get("emoji", ""); app_url = robo.get("app_url")

    with container:
        st.markdown("<div class='robot-card'>", unsafe_allow_html=True)

        st.markdown(f"### {emoji} {title}", unsafe_allow_html=True)

        state = loaded_states.get(key)
        if state is None:
            st.markdown(status_dot_html(None), unsafe_allow_html=True)
            err = errors.get(key)
            st.error(err) if err else st.warning("Arquivo de estado ainda não foi criado por este robô.")
            if app_url:
                st.link_button("Abrir app", app_url, type="primary")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        last_dt = summarize_robot_state(state)["last_update"]
        st.markdown(status_dot_html(last_dt), unsafe_allow_html=True)

        now_dt = agora_lx()
        status_badge = badge_status_tempo(last_dt)
        badges = f"{badge_pregao(now_dt)} &nbsp;&nbsp; {badge_pause(bool(state.get('pausado', False)))} &nbsp;&nbsp; {status_badge}"
        st.markdown(badges, unsafe_allow_html=True)

        summary = summarize_robot_state(state)

        c1, c2, c3 = st.columns(3)
        c1.metric("Ativos monitorados", summary["ativos_monitorados"])
        c2.metric("Disparos (sessão)", summary["total_disparos"])
        c3.metric("Alertas (histórico)", summary["total_alertas"])

        tickers = summary["tickers"] or []
        st.caption("Tickers: " + (", ".join([str(t) for t in tickers]) if tickers else "—"))

        fig = build_sparkline(state)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"plot_{key}")
        else:
            st.caption("Sem histórico suficiente para gráfico.")

        st.markdown("**Log recente:**")
        lines = get_last_log_lines(state, 5)
        if lines:
            for ln in lines: st.code(ln, language="text")
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

        st.markdown("</div>", unsafe_allow_html=True)

for i in range(0, len(ROBOS), 2):
    with st.container():
        col_left, col_right = st.columns(2)
        render_robot_card(ROBOS[i], col_left)
        if i + 1 < len(ROBOS):
            render_robot_card(ROBOS[i + 1], col_right)
    st.markdown("---")

# ============================
# DIAGNÓSTICO DE ERROS / LOGS
# ============================

if "_tick_errors" in st.session_state and st.session_state["_tick_errors"]:
    st.markdown("### ⚠️ Erros recentes (últimos pings ou gravações no Supabase)")
    for err in reversed(st.session_state["_tick_errors"][-10:]):
        st.code(err, language="text")
else:
    st.markdown("✅ Nenhum erro recente de ping ou gravação detectado.")

# ============================
# RODAPÉ
# ============================

st.caption("© Painel Central 1Milhão — consolidado dos robôs. Mantenha cada app em execução para dados atualizados.")

