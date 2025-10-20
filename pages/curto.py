# painel_curto.py
# -*- coding: utf-8 -*-
import streamlit as st
import requests
import datetime
import json
from zoneinfo import ZoneInfo
import pandas as pd

# ==============================
# ⚙️ CONFIGURAÇÕES BÁSICAS
# ==============================
st.set_page_config(page_title="Painel CURTO PRAZO", layout="wide", page_icon="🤖")

TZ = ZoneInfo("Europe/Lisbon")
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# ==============================
# 🧠 FUNÇÕES SUPABASE
# ==============================
def carregar_estado_duravel():
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]["v"]
        else:
            st.warning("ℹ️ Nenhum estado encontrado na Supabase.")
            return {"ativos": [], "historico_alertas": [], "log_monitoramento": []}
    except Exception as e:
        st.error(f"Erro ao carregar estado remoto: {e}")
        return {"ativos": [], "historico_alertas": [], "log_monitoramento": []}


def salvar_estado_duravel(estado):
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    payload = {"k": STATE_KEY, "v": estado}
    try:
        r = requests.post(url, headers=HEADERS, data=json.dumps(payload), timeout=10)
        if r.status_code not in (200, 201, 204):
            st.error(f"Erro ao salvar: {r.text}")
        else:
            st.success("✅ Estado salvo na Supabase.")
    except Exception as e:
        st.error(f"Erro ao salvar estado: {e}")


def remover_ativo(estado, ticker):
    """Remove ativo específico e salva estado."""
    ativos_antes = len(estado.get("ativos", []))
    estado["ativos"] = [a for a in estado.get("ativos", []) if a["ticker"].upper() != ticker.upper()]

    # Também limpa auxiliares relacionados
    for campo in ("tempo_acumulado", "em_contagem", "status"):
        if isinstance(estado.get(campo), dict):
            estado[campo].pop(ticker.upper(), None)

    if len(estado["ativos"]) < ativos_antes:
        salvar_estado_duravel(estado)
        st.success(f"🧹 Ativo {ticker} removido.")
    else:
        st.warning(f"Ticker {ticker} não encontrado.")


# ==============================
# 🚀 INTERFACE PRINCIPAL
# ==============================
st.title("📈 Painel CURTO PRAZO — Visualização em tempo real")

if st.button("🔄 Atualizar estado"):
    st.session_state["estado_curto"] = carregar_estado_duravel()

# Carrega estado no primeiro acesso
if "estado_curto" not in st.session_state:
    st.session_state["estado_curto"] = carregar_estado_duravel()

estado = st.session_state["estado_curto"]

# ==============================
# 📦 ATIVOS ATUAIS
# ==============================
st.subheader("📦 Ativos monitorados")

if estado.get("ativos"):
    df = pd.DataFrame(estado["ativos"])
    st.dataframe(df, use_container_width=True)

    tickers = [a["ticker"] for a in estado["ativos"]]
    ticker_remover = st.selectbox("🧹 Remover ativo", [""] + tickers)
    if ticker_remover:
        if st.button(f"Remover {ticker_remover}"):
            remover_ativo(estado, ticker_remover)
            st.session_state["estado_curto"] = carregar_estado_duravel()
            st.rerun()
else:
    st.info("Nenhum ativo monitorado no momento.")

# ==============================
# ➕ INSERIR NOVO ATIVO
# ==============================
st.subheader("➕ Inserir novo ativo")

with st.form("inserir_ativo"):
    col1, col2, col3 = st.columns(3)
    with col1:
        ticker = st.text_input("Ticker (ex: PETR4)").upper()
    with col2:
        preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)
    with col3:
        operacao = st.selectbox("Operação", ["compra", "venda"])
    enviar = st.form_submit_button("💾 Inserir ativo")

if enviar:
    if ticker and preco > 0:
        novo = {"ticker": ticker, "preco": preco, "operacao": operacao}
        estado.setdefault("ativos", []).append(novo)
        salvar_estado_duravel(estado)
        st.session_state["estado_curto"] = carregar_estado_duravel()
        st.success(f"✅ Ativo {ticker} adicionado.")
        st.rerun()
    else:
        st.error("⚠️ Preencha todos os campos corretamente.")

# ==============================
# 🧾 HISTÓRICO DE ALERTAS
# ==============================
st.subheader("🧾 Histórico de alertas")

if estado.get("historico_alertas"):
    df_hist = pd.DataFrame(estado["historico_alertas"])
    st.dataframe(df_hist, use_container_width=True)
else:
    st.info("Nenhum alerta registrado ainda.")

# ==============================
# 🧠 LOG DE MONITORAMENTO
# ==============================
st.subheader("🧠 Log do robô (direto do Render)")

if estado.get("log_monitoramento"):
    logs = estado["log_monitoramento"][-300:][::-1]  # últimos 300, ordem reversa
    log_text = "\n".join(logs)
    st.text_area("Log recente", log_text, height=300)
else:
    st.info("Sem logs registrados ainda.")

# ==============================
# 📅 INFO
# ==============================
st.caption(f"Atualizado em: {datetime.datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
