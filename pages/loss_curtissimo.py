# -*- coding: utf-8 -*-
"""
loss_curtissimo.py
CARTEIRA CURTÍSSIMO PRAZO - STOP LOSS (Streamlit)
"""

import streamlit as st
st.set_page_config(page_title="CARTEIRA LOSS CURTÍSSIMO", layout="wide")

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
SAVE_PATH = os.path.join(SAVE_DIR, "state_loss_curtissimo.json")


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
            st.sidebar.info("💾 Estado (LOSS CURTÍSSIMO) restaurado com sucesso!")
        except Exception as e:
            st.sidebar.error(f"Erro ao carregar estado: {e}")

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"] = remetente
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(remetente, senha_ou_token)
        server.send_message(msg)


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


# -----------------------------
# ESTADO INICIAL
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status",
                                               "precos_historicos", "ultimo_update_tempo"] else []

if "pausado" not in st.session_state:
    st.session_state.pausado = False
if "ultimo_estado_pausa" not in st.session_state:
    st.session_state.ultimo_estado_pausa = None
if "disparos" not in st.session_state:
    st.session_state.disparos = {}

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
        st.session_state.log_monitoramento = []
        st.sidebar.success("✅ Estado (LOSS CURTÍSSIMO) apagado e reiniciado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")


async def testar_telegram():
    token = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_losscurtissimo", "")
    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat, text="✅ Teste de alerta LOSS CURTÍSSIMO funcionando!")
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
st.title("🛑 LOSS CURTÍSSIMO - STOP LOSS")
st.caption(
    f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
    f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}"
)
st.subheader("🕒 Log de Monitoramento")
countdown_container = st.empty()
log_container = st.empty()

# -----------------------------
# AVISO DE ABERTURA DO PREGÃO
# -----------------------------
if not st.session_state.get("avisou_abertura_pregao", False):
    st.session_state["avisou_abertura_pregao"] = True
    try:
        token = st.secrets.get("telegram_token", "").strip()
        chat = st.secrets.get("telegram_chat_id_losscurtissimo", "").strip()
        if token and chat:
            bot = Bot(token=token)
            asyncio.run(bot.send_message(chat_id=chat, text="🛑 Robô LOSS CURTÍSSIMO ativo — Pregão Aberto! ⏱️"))
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | 📣 Telegram: Pregão Aberto (LOSS CURTÍSSIMO)"
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
# 🧪 PAINEL DE DEBUG / BACKUP
# -----------------------------
with st.expander("🧪 Debug / Backup do estado", expanded=False):
    st.caption(f"Arquivo: `{SAVE_PATH}`")
    try:
        if os.path.exists(SAVE_PATH):
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                state_preview = json.load(f)
            st.json(state_preview)
            st.download_button(
                "⬇️ Baixar state_loss_curtissimo.json",
                data=json.dumps(state_preview, ensure_ascii=False, indent=2),
                file_name="state_loss_curtissimo.json",
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


