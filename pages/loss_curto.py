# LOSS_CURTO.PY - INTERFACE VISUAL (LEITURA/INSERÇÃO NA SUPABASE; SEM ALTERAR/DELETAR NA NUVEM)
# -*- coding: utf-8 -*-

import streamlit as st
from yahooquery import Ticker
import datetime
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import re
import json
import os
import time

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="🛑 LOSS CURTO — ENCERRAMENTO/STOP", layout="wide")

try:
    st.autorefresh(interval=120 * 1000, key="refresh_loss_curto")
except Exception:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=120 * 1000, key="refresh_loss_curto")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(3, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(23, 59, 0)

INTERVALO_VERIFICACAO = 120
TEMPO_ACUMULADO_MAXIMO = 360
LOG_MAX_LINHAS = 1000

PALETTE = [
    "#ef4444", "#3b82f6", "#f59e0b", "#10b981", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# -----------------------------
# SUPABASE
# -----------------------------
SUPABASE_URL = st.secrets["supabase_url_loss_curto"]
SUPABASE_KEY = st.secrets["supabase_key_loss_curto"]
SUPABASE_TABLE = "kv_state_losscurto"
STATE_KEY = "loss_curto_przo_v1"

def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def ler_ativos_da_supabase() -> list[dict]:
    """Lê os ativos de v['ativos'] na linha (k) 'loss_curto_przo_v1'."""
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?k=eq.{STATE_KEY}&select=v"
    try:
        r = requests.get(url, headers=_sb_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return []
        estado = data[0].get("v", {})
        ativos = estado.get("ativos", [])
        norm = []
        for a in ativos:
            t = (a.get("ticker") or "").upper().strip()
            op = (a.get("operacao") or "").lower().strip()
            pr = a.get("preco")
            if t and op in ("compra", "venda") and isinstance(pr, (int, float)):
                norm.append({"ticker": t, "operacao": op, "preco": float(pr)})
        return norm
    except Exception as e:
        st.sidebar.error(f"⚠️ Erro ao ler Supabase: {e}")
        return []

def inserir_ativo_na_supabase(ticker: str, operacao: str, preco: float) -> tuple[bool, str | None]:
    """Insere um novo ativo no array v['ativos'] (merge) da chave 'loss_curto_przo_v1'."""
    try:
        url_get = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?k=eq.{STATE_KEY}&select=v"
        r = requests.get(url_get, headers=_sb_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        estado = data[0].get("v", {}) if data else {}

        ativos = estado.get("ativos", [])
        novo = {"ticker": ticker.upper().strip(), "operacao": operacao.lower().strip(), "preco": float(preco)}
        ativos.append(novo)
        estado["ativos"] = ativos

        payload = {"k": STATE_KEY, "v": estado}
        r2 = requests.post(
            f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
            headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates"},
            json=payload,
            timeout=15,
        )
        r2.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)

# -----------------------------
# ESTADO LOCAL
# -----------------------------
VIS_STATE_FILE = "session_data/visual_state_loss_curto.json"

def carregar_visual_state():
    os.makedirs("session_data", exist_ok=True)
    if os.path.exists(VIS_STATE_FILE):
        try:
            with open(VIS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            precos = {
                t: [(datetime.datetime.fromisoformat(dt), v) for dt, v in pares]
                for t, pares in data.get("precos_historicos", {}).items()
            }
            disparos = {
                t: [(datetime.datetime.fromisoformat(dt), v) for dt, v in pares]
                for t, pares in data.get("disparos", {}).items()
            }
            st.session_state.precos_historicos = precos
            st.session_state.disparos = disparos
        except Exception as e:
            st.sidebar.warning(f"⚠️ Visual state corrompido: {e}")

def salvar_visual_state():
    try:
        os.makedirs("session_data", exist_ok=True)
        data = {
            "precos_historicos": {
                t: [(dt.isoformat(), v) for dt, v in pares]
                for t, pares in st.session_state.get("precos_historicos", {}).items()
            },
            "disparos": {
                t: [(dt.isoformat(), v) for dt, v in pares]
                for t, pares in st.session_state.get("disparos", {}).items()
            }
        }
        with open(VIS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro salvando visual state: {e}")

def inicializar_estado():
    defaults = {
        "log_monitoramento": [],
        "ticker_colors": {},
        "tempo_acumulado": {},
        "em_contagem": {},
        "status": {},
        "precos_historicos": {},
        "disparos": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    carregar_visual_state()

inicializar_estado()

# -----------------------------
# UTILITÁRIOS
# -----------------------------
def agora_lx():
    return datetime.datetime.now(TZ)

def dentro_pregao(dt):
    t = dt.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

@st.cache_data(ttl=5)
def obter_preco_atual(ticker_symbol: str):
    if not ticker_symbol.endswith(".SA"):
        ticker_symbol = f"{ticker_symbol}.SA"
    tk = Ticker(ticker_symbol)
    try:
        price_data = tk.price
        if isinstance(price_data, dict):
            symbol_data = price_data.get(ticker_symbol)
            if isinstance(symbol_data, dict):
                p = (symbol_data.get("regularMarketPrice")
                     or symbol_data.get("postMarketPrice")
                     or symbol_data.get("preMarketPrice"))
                if isinstance(p, (int, float)):
                    return float(p)
    except Exception:
        pass
    return "-"

def color_for_ticker(ticker):
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]

# -----------------------------
# INTERFACE
# -----------------------------
st.sidebar.header("⚙️ Configurações (LOSS CURTO)")

# Teste Telegram
async def testar_telegram():
    tok = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_losscurto", "")
    try:
        if tok and chat:
            bot = Bot(token=tok)
            await bot.send_message(chat_id=chat, text="✅ Teste de alerta LOSS CURTO funcionando!")
            return True, None
        return False, "token/chat_id não configurado"
    except Exception as e:
        return False, str(e)

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")

# -----------------------------
# FORM DE INSERÇÃO
# -----------------------------
st.title("🛑 LOSS CURTO — ENCERRAMENTO/STOP")
st.markdown("<hr>", unsafe_allow_html=True)
col1, col2, col3 = st.columns(3)
with col1:
    ticker_input = st.text_input("Ticker (ex: PETR4)").upper().strip()
with col2:
    operacao_input = st.selectbox("Operação original (a encerrar)", ["compra", "venda"])
with col3:
    preco_input = st.number_input("Preço STOP (gatilho de encerramento)", min_value=0.01, step=0.01)

if st.button("➕ Adicionar STOP"):
    if not ticker_input:
        st.error("Digite um ticker válido.")
    else:
        ok, erro = inserir_ativo_na_supabase(ticker_input, operacao_input, preco_input)
        if ok:
            st.success(f"STOP de {ticker_input} inserido na Supabase (LOSS).")
        else:
            st.error(f"Falha ao inserir: {erro}")

# -----------------------------
# BLOCO PRINCIPAL (TABELA + GRÁFICO)
# -----------------------------
ativos = ler_ativos_da_supabase()
st.subheader("🧠 Banco de Dados (LOSS)")
tabela_status = st.empty()
grafico = st.empty()

now = agora_lx()
if ativos:
    linhas = []
    for a in ativos:
        t = a["ticker"]
        preco_stop = a["preco"]
        op = a["operacao"].upper()
        preco_atual = obter_preco_atual(t)

        condicao = (
            (a["operacao"] == "compra" and preco_atual != "-" and preco_atual <= preco_stop) or
            (a["operacao"] == "venda"  and preco_atual != "-" and preco_atual >= preco_stop)
        )

        st.session_state.status[t] = "🟡 Em contagem (STOP)" if condicao else "🟢 Monitorando"

        linhas.append({
            "Ticker": t,
            "Operação": op,
            "STOP": f"R$ {preco_stop:.2f}",
            "Preço Atual": f"{preco_atual}" if preco_atual != "-" else "-",
            "Status": st.session_state.status[t],
        })

        if preco_atual != "-":
            st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))

        if condicao:
            if t not in st.session_state.disparos:
                st.session_state.disparos[t] = []
            st.session_state.disparos[t].append((now, preco_atual))

    df = pd.DataFrame(linhas)
    tabela_status.dataframe(df, use_container_width=True, height=240)
else:
    tabela_status.info("Nenhum STOP encontrado na Supabase (LOSS).")

# -----------------------------
# GRÁFICO
# -----------------------------
fig = go.Figure()
for t, dados in st.session_state.precos_historicos.items():
    if dados:
        xs, ys = zip(*dados)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t,
                                 line=dict(color=color_for_ticker(t), width=2)))
for t, pontos in st.session_state.disparos.items():
    if pontos:
        xs, ys = zip(*pontos)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name=f"Encerramento {t}",
                                 marker=dict(symbol="x", size=12, line=dict(width=2, color="white"))))

fig.update_layout(title="📉 Evolução dos Preços (LOSS / STOP)", template="plotly_dark")
grafico.plotly_chart(fig, use_container_width=True)

salvar_visual_state()
