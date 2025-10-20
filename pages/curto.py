# -*- coding: utf-8 -*-
import streamlit as st
from yahooquery import Ticker
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import re
import streamlit.components.v1 as components
import json
from streamlit_autorefresh import st_autorefresh
import time

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)

INTERVALO_VERIFICACAO = 300
TEMPO_ACUMULADO_MAXIMO = 1500
LOG_MAX_LINHAS = 1000
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# =============================
# PERSISTÊNCIA — SOMENTE SUPABASE
# =============================
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"

def agora_lx():
    return datetime.datetime.now(TZ)

def _estado_snapshot():
    snapshot = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "tempo_acumulado": st.session_state.get("tempo_acumulado", {}),
        "em_contagem": st.session_state.get("em_contagem", {}),
        "status": st.session_state.get("status", {}),
        "ultimo_update_tempo": st.session_state.get("ultimo_update_tempo", {}),
        "pausado": st.session_state.get("pausado", False),
        "ultimo_estado_pausa": st.session_state.get("ultimo_estado_pausa", None),
        "ultimo_ping_keepalive": st.session_state.get("ultimo_ping_keepalive", None),
        "ultima_data_abertura_enviada": st.session_state.get("ultima_data_abertura_enviada", None),
    }
    snapshot["precos_historicos"] = {
        t: [(dt.isoformat() if isinstance(dt, datetime.datetime) else dt, preco)
            for dt, preco in dados]
        for t, dados in st.session_state.get("precos_historicos", {}).items()
    }
    snapshot["disparos"] = {
        t: [(dt.isoformat() if isinstance(dt, datetime.datetime) else dt, preco)
            for dt, preco in pontos]
        for t, pontos in st.session_state.get("disparos", {}).items()
    }
    return snapshot

def salvar_estado_duravel(force: bool = False):
    last = st.session_state.get("__last_save_ts")
    now_ts = agora_lx().timestamp()
    if not force and last and (now_ts - last) < PERSIST_DEBOUNCE_SECONDS:
        return
    snapshot = _estado_snapshot()
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    payload = {"k": STATE_KEY, "v": snapshot}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        if r.status_code in (200, 201, 204):
            st.session_state["__last_save_ts"] = now_ts
            st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | ☁️ Estado salvo na nuvem")
        else:
            st.sidebar.error(f"⚠️ Erro ao salvar: {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar no Supabase: {e}")

def carregar_estado_duravel():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            for k, v in estado.items():
                if k in ["precos_historicos", "disparos"]:
                    reconv = {t: [(datetime.datetime.fromisoformat(dt), p) for dt, p in dados] for t, dados in v.items()}
                    st.session_state[k] = reconv
                else:
                    st.session_state[k] = v
            st.session_state["origem_estado"] = "☁️ Supabase"
            st.sidebar.success("☁️ Estado carregado da nuvem!")
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto encontrado.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar: {e}")

def apagar_estado_remoto():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
    try:
        r = requests.delete(url, headers=headers, timeout=15)
        if r.status_code == 204:
            st.sidebar.success("✅ Estado remoto apagado!")
        else:
            st.sidebar.error(f"Erro ao apagar: {r.status_code}")
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar: {e}")

# -----------------------------
# INICIALIZAÇÃO
# -----------------------------
def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def inicializar_estado():
    defaults = {
        "ativos": [], "historico_alertas": [], "log_monitoramento": [],
        "tempo_acumulado": {}, "em_contagem": {}, "status": {},
        "precos_historicos": {}, "ultimo_update_tempo": {},
        "pausado": False, "disparos": {}, "__last_save_ts": None,
        "__carregado_ok__": False, "ultima_data_abertura_enviada": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    ensure_color_map()

inicializar_estado()
carregar_estado_duravel()
st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | Robô iniciado - Execução Render ativa")

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(dest, assunto, corpo, rem, senha):
    try:
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Subject"] = rem, dest, assunto
        msg.attach(MIMEText(corpo, "html"))
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(rem, senha)
            s.send_message(msg)
        return True
    except Exception:
        return False

async def enviar_telegram(msg):
    try:
        tok = st.secrets["telegram_token"]
        chat = st.secrets["telegram_chat_id_curto"]
        bot = Bot(token=tok)
        await bot.send_message(chat_id=chat, text=msg, parse_mode="HTML")
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ Erro Telegram: {e}")

@st.cache_data(ttl=5)
def obter_preco_atual(tk):
    try:
        t = Ticker(tk)
        p = t.price.get(tk, {}).get("regularMarketPrice")
        return float(p) if p else float(t.history(period="1d")["close"].iloc[-1])
    except Exception:
        return None

def notificar(ticker, alvo, atual, operacao):
    msg = f"""
💥 <b>ALERTA {operacao.upper()}!</b>
<b>{ticker}</b> atingiu o preço alvo.
Alvo: R$ {alvo:.2f} | Atual: R$ {atual:.2f}
"""
    asyncio.run(enviar_telegram(msg))

# -----------------------------
# INTERFACE
# -----------------------------
st.title("📈 CURTO PRAZO - COMPRA E VENDA")
st.caption(f"Agora: {agora_lx().strftime('%Y-%m-%d %H:%M:%S %Z')}")

if st.sidebar.button("🧹 Limpar Estado Remoto"):
    apagar_estado_remoto()
    st.session_state.clear()
    inicializar_estado()
    st.sidebar.success("Reset concluído.")

st.sidebar.checkbox("⏸️ Pausar monitoramento", key="pausado")

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
    if ticker:
        ativo = {"ticker": ticker, "operacao": operacao, "preco": preco}
        st.session_state.ativos.append(ativo)
        st.session_state.status[ticker] = "🟢 Monitorando"
        salvar_estado_duravel(force=True)
        st.success(f"{ticker} adicionado com sucesso.")

# -----------------------------
# LOOP DE MONITORAMENTO
# -----------------------------
now = agora_lx()
if not st.session_state.pausado and st.session_state.ativos:
    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        alvo = ativo["preco"]
        op = ativo["operacao"]
        atual = obter_preco_atual(f"{t}.SA")
        if atual is None:
            continue
        cond = (op == "compra" and atual >= alvo) or (op == "venda" and atual <= alvo)
        if cond:
            notificar(t, alvo, atual, op)
            st.session_state.status[t] = "🚀 Disparado"
            salvar_estado_duravel(force=True)
            st.warning(f"{t} atingiu alvo!")

# -----------------------------
# GRÁFICO E LOG
# -----------------------------
st.subheader("📊 Ativos Monitorados")
if st.session_state.ativos:
    df = pd.DataFrame(st.session_state.ativos)
    st.dataframe(df, use_container_width=True)
else:
    st.info("Nenhum ativo monitorado.")

st.subheader("🕒 Log")
for line in st.session_state.log_monitoramento[-20:][::-1]:
    st.text(line)

# -----------------------------
# AUTOREFRESH
# -----------------------------
st_autorefresh(interval=300_000, limit=None, key="curto-refresh")
