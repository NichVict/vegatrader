# -*- coding: utf-8 -*-
"""
curtissimo.py
CARTEIRA CURTÍSSIMO PRAZO - COMPRA E VENDA (Streamlit)
"""

import streamlit as st
st.set_page_config(page_title="CARTEIRA CURTÍSSIMO PRAZO", layout="wide")

from yahooquery import Ticker
import datetime
import time
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
import uuid
import streamlit.components.v1 as components
import json
import os

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)

INTERVALO_VERIFICACAO = 300
TEMPO_ACUMULADO_MAXIMO = 900  # 15 minutos
LOG_MAX_LINHAS = 1000

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# ==== PERSISTÊNCIA LOCAL ====
SAVE_DIR = "session_data"
os.makedirs(SAVE_DIR, exist_ok=True)
SAVE_PATH = os.path.join(SAVE_DIR, "state_curtissimo.json")


def salvar_estado():
    estado = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "disparos": st.session_state.get("disparos", {}),
        "tempo_acumulado": st.session_state.get("tempo_acumulado", {}),
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "pausado": st.session_state.get("pausado", False),
        "ultimo_estado_pausa": st.session_state.get("ultimo_estado_pausa", None),
        "ultimo_ping_keepalive": st.session_state.get("ultimo_ping_keepalive", None),
        "avisou_abertura_pregao": st.session_state.get("avisou_abertura_pregao", False),
        "ultimo_update_tempo": st.session_state.get("ultimo_update_tempo", {}),
    }
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, default=str, indent=2)
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado: {e}")


def carregar_estado():
    if os.path.exists(SAVE_PATH):
        try:
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                estado = json.load(f)
            pausado_atual = st.session_state.get("pausado")
            for k, v in estado.items():
                if k == "pausado" and pausado_atual is not None:
                    continue
                st.session_state[k] = v
            st.sidebar.info("💾 Estado (CURTÍSSIMO) restaurado!")
        except Exception as e:
            st.sidebar.error(f"Erro ao carregar estado: {e}")


# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    mensagem = MIMEMultipart()
    mensagem["From"] = remetente
    mensagem["To"] = destinatario
    mensagem["Subject"] = assunto
    mensagem.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.starttls()
        servidor.login(remetente, senha_ou_token)
        servidor.send_message(mensagem)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol):
    tk = Ticker(ticker_symbol)
    try:
        p = tk.price.get(ticker_symbol, {}).get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    preco_atual = tk.history(period="3d")["close"].iloc[-1]
    return float(preco_atual)


def agora_lx():
    return datetime.datetime.now(TZ)


def dentro_pregao(dt_now):
    t = dt_now.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO


def segundos_ate_abertura(dt_now):
    hoje_abre = dt_now.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=0, second=0, microsecond=0)
    hoje_fecha = dt_now.replace(hour=HORARIO_FIM_PREGAO.hour, minute=0, second=0, microsecond=0)
    if dt_now < hoje_abre:
        return int((hoje_abre - dt_now).total_seconds()), hoje_abre
    elif dt_now > hoje_fecha:
        amanha_abre = hoje_abre + datetime.timedelta(days=1)
        return int((amanha_abre - dt_now).total_seconds()), amanha_abre
    else:
        return 0, hoje_abre


def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}


def color_for_ticker(ticker):
    ensure_color_map()
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]


TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\.SA\b")
PLAIN_TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\b")


def extract_ticker(line):
    m = TICKER_PAT.search(line)
    if m:
        return m.group(1)
    m2 = PLAIN_TICKER_PAT.search(line)
    return m2.group(1) if m2 else None


def render_log_html(lines, selected_tickers=None, max_lines=200):
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]
    if selected_tickers:
        subset = [l for l in subset if (extract_ticker(l) in selected_tickers)]
    css = """
    <style>
      .log-card {
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 10px;
        padding: 10px 12px;
        max-height: 360px;
        overflow-y: auto;
      }
      .log-line{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px; line-height: 1.35; margin: 2px 0; color: #e5e7eb;
        display: flex; align-items: baseline; gap: 8px;
      }
      .ts{ color:#9ca3af; min-width:64px; text-align:right; }
      .badge{ display:inline-block; padding:1px 8px; font-size:12px; border-radius:9999px; color:white; }
      .msg{ white-space: pre-wrap; }
    </style>
    """
    html = [css, "<div class='log-card'>"]
    for l in subset:
        if " | " in l:
            ts, rest = l.split(" | ", 1)
        else:
            ts, rest = "", l
        tk = extract_ticker(l)
        badge_html = f"<span class='badge' style='background:{color_for_ticker(tk)}'>{tk}</span>" if tk else ""
        html.append(f"<div class='log-line'><span class='ts'>{ts}</span>{badge_html}<span class='msg'>{rest}</span></div>")
    html.append("</div>")
    st.markdown("\n".join(html), unsafe_allow_html=True)


# -----------------------------
# ESTADOS INICIAIS
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"] else []

if "pausado" not in st.session_state:
    st.session_state.pausado = False
if "ultimo_estado_pausa" not in st.session_state:
    st.session_state.ultimo_estado_pausa = None
if "disparos" not in st.session_state:
    st.session_state.disparos = {}
ensure_color_map()

carregar_estado()

# -----------------------------
# SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("🧹 Apagar estado salvo (reset total)"):
    try:
        if os.path.exists(SAVE_PATH):
            os.remove(SAVE_PATH)
        st.session_state.clear()
        st.session_state.pausado = True
        st.session_state.ultimo_estado_pausa = None
        st.session_state.ativos = []
        st.session_state.historico_alertas = []
        st.session_state.log_monitoramento = []
        st.session_state.tempo_acumulado = {}
        st.session_state.em_contagem = {}
        st.session_state.status = {}
        st.session_state.precos_historicos = {}
        st.session_state.disparos = {}
        now_tmp = agora_lx()
        st.session_state.log_monitoramento.append(f"{now_tmp.strftime('%H:%M:%S')} | 🧹 Reset manual (CURTÍSSIMO)")
        salvar_estado()
        st.sidebar.success("✅ Estado apagado e reiniciado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")


async def testar_telegram():
    token = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_curtissimo", "")
    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat, text="✅ Teste de alerta CURTÍSSIMO funcionando!")
        return True, None
    except Exception as e:
        return False, str(e)


if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste (usando st.secrets)...")
    ok, erro = asyncio.run(testar_telegram())
    if ok:
        st.sidebar.success("✅ Mensagem enviada com sucesso!")
    else:
        st.sidebar.error(f"❌ Falha: {erro}")

st.sidebar.checkbox("⏸️ Pausar monitoramento (modo edição)", key="pausado")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("⚡ CURTÍSSIMO - COMPRA E VENDA")
st.caption(
    f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
    f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}"
)

st.subheader("🕒 Log de Monitoramento")
countdown_container = st.empty()
log_container = st.empty()

# -----------------------------
# ENVIO DE NOTIFICAÇÃO DE ABERTURA
# -----------------------------
if not st.session_state.get("avisou_abertura_pregao", False):
    st.session_state["avisou_abertura_pregao"] = True
    try:
        token = st.secrets.get("telegram_token", "").strip()
        chat = st.secrets.get("telegram_chat_id_curtissimo", "").strip()
        if token and chat:
            bot = Bot(token=token)
            asyncio.run(bot.send_message(chat_id=chat, text="⚡ Robô CURTÍSSIMO ativo — Pregão Aberto! 📈"))
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | 📣 Telegram: Pregão Aberto (CURTÍSSIMO)"
            )
        else:
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | ⚠️ Aviso: token/chat_id não configurado — notificação ignorada."
            )
    except Exception as e:
        st.session_state.log_monitoramento.append(
            f"{now.strftime('%H:%M:%S')} | ⚠️ Erro real ao enviar notificação de abertura: {e}"
        )

# -----------------------------
# 🧪 Debug / Backup
# -----------------------------
with st.expander("🧪 Debug / Backup do estado", expanded=False):
    st.caption(f"Arquivo: `{SAVE_PATH}`")
    try:
        if os.path.exists(SAVE_PATH):
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                state_preview = json.load(f)
            st.json(state_preview)
            st.download_button(
                "⬇️ Baixar state_curtissimo.json",
                data=json.dumps(state_preview, ensure_ascii=False, indent=2),
                file_name="state_curtissimo.json",
                mime="application/json",
            )
        else:
            st.info("Ainda não existe arquivo salvo.")
    except Exception as e:
        st.error(f"Erro ao exibir JSON: {e}")

# Salva antes de dormir
salvar_estado()

# Reexecução
time.sleep(INTERVALO_VERIFICACAO)
st.rerun()
