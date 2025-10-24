# CURTO.PY – Interface Operacional (Somente Envia / Lê da Nuvem)
# -*- coding: utf-8 -*-

import streamlit as st
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import json
import os
from streamlit_autorefresh import st_autorefresh
import re

# -----------------------------
# CONFIGURAÇÃO INICIAL
# -----------------------------
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA", layout="wide")

try:
    st.cache_data.clear()
    st.cache_resource.clear()
except Exception:
    pass

# -----------------------------
# CONSTANTES
# -----------------------------
TZ = ZoneInfo("Europe/Lisbon")
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"

LOCAL_STATE_FILE = "session_data/state_curto.json"

def agora_lx():
    return datetime.datetime.now(TZ)

# -----------------------------
# TICKERS – normalização e exibição
# -----------------------------
def normalizar_ticker(tk: str) -> str:
    """Garante formato padronizado (.SA)."""
    if not tk:
        return tk
    tk = tk.strip().upper()
    if "." not in tk:
        tk = f"{tk}.SA"
    return tk

def mostrar_ticker(tk: str) -> str:
    """Remove o sufixo .SA para exibição."""
    return tk.replace(".SA", "")

# -----------------------------
# ESTADO LOCAL
# -----------------------------
def inicializar_estado():
    defaults = {
        "ativos": [],
        "historico_alertas": [],
        "log_monitoramento": [],
        "status": {},
        "precos_historicos": {},
        "disparos": {},
        "__carregado_ok__": False,
        "origem_estado": "❓"
    }
    for k, v in defaults.items():
        st.session_state[k] = v

def carregar_estado_duravel():
    """Lê o estado atual do Supabase (tabela kv_state_curto, coluna v)."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json"
    }
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?select=v&k=eq.{STATE_KEY}"
    origem = "❌ Nenhum"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200 and r.json():
            payload = r.json()[0]["v"]

            # Se vier como string, tenta decodificar
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}

            # Normaliza tickers para padrão com .SA
            ativos = payload.get("ativos", [])
            for ativo in ativos:
                ativo["ticker"] = normalizar_ticker(ativo.get("ticker", ""))

            # Atualiza session_state
            for k, v in payload.items():
                st.session_state[k] = v
            st.session_state["ativos"] = ativos
            st.session_state["origem_estado"] = "☁️ Supabase"
            st.session_state["__carregado_ok__"] = True
            st.sidebar.success("✅ Estado carregado da nuvem.")
        else:
            st.sidebar.warning("ℹ️ Nenhum estado remoto encontrado.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")

# -----------------------------
# SALVAR ESTADO
# -----------------------------
def salvar_estado_duravel(force: bool = False):
    """Salva o estado atual (delete + insert)."""
    snapshot = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "disparos": st.session_state.get("disparos", {})
    }
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"k": STATE_KEY, "v": snapshot}
    try:
        # Remove o registro anterior
        requests.delete(f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}", headers=headers)
        # Insere novamente
        r = requests.post(f"{SUPABASE_URL}/rest/v1/{TABLE}", headers=headers, data=json.dumps(payload))
        if r.status_code not in (200, 201, 204):
            st.sidebar.error(f"Erro ao salvar estado remoto: {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado remoto: {e}")

# -----------------------------
# NOTIFICAÇÕES / TESTES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = remetente, destinatario, assunto
    msg.attach(MIMEText(corpo, "html" if "<html" in corpo.lower() else "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(remetente, senha_ou_token)
        s.send_message(msg)

def formatar_mensagem_alerta(ticker, preco_alvo, preco_atual, operacao):
    ticker_simples = mostrar_ticker(ticker)
    tipo = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    msg_tg = f"""
💥 <b>ALERTA DE {tipo.upper()} ATIVADA!</b>\n
<b>Ticker:</b> {ticker_simples}\n
<b>Preço alvo:</b> R$ {preco_alvo:.2f}\n
<b>Preço atual:</b> R$ {preco_atual:.2f}\n\n
📊 <a href='https://br.tradingview.com/symbols/{ticker_simples}'>Ver gráfico</a>
"""
    msg_email = f"""
<html><body style="background:#0b1220;color:#e5e7eb;font-family:Arial;">
<h2 style="color:#3b82f6;">💥 ALERTA DE {tipo.upper()} ATIVADA!</h2>
<p><b>Ticker:</b> {ticker_simples}</p>
<p><b>Preço alvo:</b> R$ {preco_alvo:.2f}</p>
<p><b>Preço atual:</b> R$ {preco_atual:.2f}</p>
<p>📊 <a href="https://br.tradingview.com/symbols/{ticker_simples}" style="color:#60a5fa;">Abrir gráfico</a></p>
</body></html>
"""
    return msg_tg.strip(), msg_email.strip()

def enviar_notificacao_curto(dest, assunto, corpo_email, rem, senha, token_tg, chat_id, corpo_tg=None):
    if senha and dest and rem:
        try:
            enviar_email(dest, assunto, corpo_email, rem, senha)
        except Exception as e:
            st.sidebar.warning(f"⚠️ Falha e-mail: {e}")

    async def send_tg():
        try:
            if token_tg and chat_id:
                bot = Bot(token=token_tg)
                await bot.send_message(chat_id=chat_id, text=corpo_tg or corpo_email, parse_mode="HTML")
        except Exception as e:
            st.sidebar.warning(f"⚠️ Falha Telegram: {e}")

    asyncio.run(send_tg())

async def testar_telegram():
    tok = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_curto", "")
    try:
        if tok and chat:
            bot = Bot(token=tok)
            await bot.send_message(chat_id=chat, text="✅ Teste de alerta CURTO PRAZO funcionando!")
            return True, None
        return False, "token/chat_id não configurado"
    except Exception as e:
        return False, str(e)

# -----------------------------
# INICIALIZAÇÃO
# -----------------------------
inicializar_estado()
carregar_estado_duravel()

# -----------------------------
# SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Enviado!" if ok else f"❌ {erro}")

if st.sidebar.button("📩 Testar Mensagem"):
    st.sidebar.info("Gerando alerta simulado...")
    try:
        tkr = "PETR4.SA"
        preco_alvo = 37.5
        preco_atual = 37.52
        oper = "compra"
        msg_tg, msg_email = formatar_mensagem_alerta(tkr, preco_alvo, preco_atual, oper)
        enviar_notificacao_curto(
            st.secrets.get("email_recipient_curto", ""),
            f"ALERTA CURTO PRAZO: {oper.upper()} em {mostrar_ticker(tkr)}",
            msg_email,
            st.secrets.get("email_sender", ""),
            st.secrets.get("gmail_app_password", ""),
            st.secrets.get("telegram_token", ""),
            st.secrets.get("telegram_chat_id_curto", ""),
            msg_tg
        )
        st.sidebar.success("✅ Mensagem enviada.")
    except Exception as e:
        st.sidebar.error(f"Erro: {e}")

if st.sidebar.button("🧹 Limpar Dados"):
    inicializar_estado()
    salvar_estado_duravel()
    st.sidebar.success("🧹 Estado local e remoto limpo.")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
st.title("📈 CURTO PRAZO - COMPRA E VENDA")
st.caption(f"Origem: {st.session_state.get('origem_estado', '❓')}")

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
    if ticker:
        tk_norm = normalizar_ticker(ticker)
        ativos = st.session_state.get("ativos", [])
        if not any(a["ticker"] == tk_norm for a in ativos):
            ativos.append({"ticker": tk_norm, "operacao": operacao, "preco": float(preco)})
            st.session_state["ativos"] = ativos
            salvar_estado_duravel()
            st.success(f"{tk_norm} enviado à nuvem.")
        else:
            st.warning("Ticker já existe.")

# -----------------------------
# STATUS DOS ATIVOS
# -----------------------------
st.subheader("📊 Status dos Ativos")
status_map = {
    "monitorando": "🟢 Monitorando",
    "em_contagem": "🟡 Em contagem",
    "disparado": "🚀 Disparado"
}
data = []
for ativo in st.session_state.get("ativos", []):
    t = ativo["ticker"]
    raw = str(st.session_state.get("status", {}).get(t, "")).lower()
    status_fmt = status_map.get(raw, "⏳ Aguardando robô da nuvem")
    data.append({
        "Ticker": mostrar_ticker(t),
        "Operação": ativo["operacao"].upper(),
        "Preço Alvo": f"R$ {float(ativo['preco']):.2f}",
        "Status": status_fmt
    })
if data:
    st.dataframe(pd.DataFrame(data), use_container_width=True, height=250)
else:
    st.info("Nenhum ativo cadastrado.")

# -----------------------------
# GRÁFICO
# -----------------------------
st.subheader("📈 Evolução dos Preços (Robô da Nuvem)")
ativos_set = {a["ticker"] for a in st.session_state.get("ativos", [])}
fig = go.Figure()
for t, dados in st.session_state.get("precos_historicos", {}).items():
    if not dados or t not in ativos_set:
        continue
    xs, ys = [], []
    for dtv, pv in dados:
        try:
            xs.append(datetime.datetime.fromisoformat(dtv))
        except Exception:
            xs.append(dtv)
        ys.append(pv)
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=mostrar_ticker(t)))
fig.update_layout(template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG
# -----------------------------
st.subheader("🕒 Monitoramento")
log_lines = st.session_state.get("log_monitoramento", [])
ativos_fmt = set(a["ticker"] for a in st.session_state.get("ativos", []))
ativos_fmt |= {t.replace(".SA", "") for t in ativos_fmt}
if ativos_fmt:
    pat = re.compile(r"\b(" + "|".join(re.escape(t) for t in ativos_fmt) + r")\b")
    log_lines = [l for l in log_lines if pat.search(l)]

st.markdown("""
<style>
  .log-card {background:#0b1220;border:1px solid #1f2937;border-radius:10px;
             padding:10px 12px;max-height:360px;overflow-y:auto;}
  .log-line {font-family: monospace;font-size:13px;color:#e5e7eb;}
</style>
""", unsafe_allow_html=True)

if log_lines:
    st.markdown("<div class='log-card'>" + "<br>".join(
        f"<div class='log-line'>{l}</div>" for l in log_lines[-300:][::-1]
    ) + "</div>", unsafe_allow_html=True)
else:
    st.info("Sem logs no momento.")

# -----------------------------
# AUTOREFRESH
# -----------------------------
st_autorefresh(interval=60000, key="curto-refresh")
