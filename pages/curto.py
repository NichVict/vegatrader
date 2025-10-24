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
import time

# -----------------------------
# CONFIGURAÇÃO INICIAL
# -----------------------------
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA", layout="wide")

# 🔥 Limpa caches de dados e recursos (evita resquícios antigos)
st.cache_data.clear()
st.cache_resource.clear()

# -----------------------------
# CONSTANTES E CONFIGURAÇÕES
# -----------------------------
TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"
LOCAL_STATE_FILE = "session_data/state_curto.json"


def agora_lx():
    return datetime.datetime.now(TZ)


# -----------------------------
# ESTADO INICIAL
# -----------------------------
def inicializar_estado():
    """Inicializa o estado da sessão, sempre limpo."""
    defaults = {
        "ativos": [],
        "historico_alertas": [],
        "log_monitoramento": [],
        "status": {},
        "precos_historicos": {},
        "disparos": {},
        "__last_save_ts": None,
        "__carregado_ok__": False,
        "ultima_data_abertura_enviada": None,
        "origem_estado": "❓"
    }
    for k, v in defaults.items():
        st.session_state[k] = v


def carregar_estado_duravel():
    """Carrega o estado salvo na nuvem (Supabase)."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    origem = "❌ Nenhum"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            for k, v in estado.items():
                st.session_state[k] = v
            origem = "☁️ Supabase"
            st.sidebar.success("✅ Estado carregado da nuvem!")
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto encontrado.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")
    st.session_state["origem_estado"] = origem
    st.session_state["__carregado_ok__"] = (origem == "☁️ Supabase")


# -----------------------------
# FUNÇÃO DE SALVAR ESTADO
# -----------------------------
def _persist_now():
    """Salva o estado atual na nuvem, sobrescrevendo completamente."""
    snapshot = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "disparos": st.session_state.get("disparos", {}),
        "ultima_data_abertura_enviada": st.session_state.get("ultima_data_abertura_enviada", None),
    }

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

    # 1️⃣ Apaga o registro anterior (evita duplicate key)
    try:
        delete_url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
        requests.delete(delete_url, headers=headers, timeout=10)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao apagar estado anterior: {e}")

    # 2️⃣ Cria novamente o registro limpo
    payload = {"k": STATE_KEY, "v": snapshot}
    insert_url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    try:
        r = requests.post(insert_url, headers=headers, data=json.dumps(payload), timeout=15)
        if r.status_code not in (200, 201, 204):
            st.sidebar.error(f"Erro ao salvar estado remoto: {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado remoto: {e}")

    st.session_state["__last_save_ts"] = agora_lx().timestamp()


def salvar_estado_duravel(force: bool = False):
    """Salva o estado com controle de debounce."""
    if force:
        _persist_now()
        return
    last = st.session_state.get("__last_save_ts")
    now_ts = agora_lx().timestamp()
    if not last or (now_ts - last) >= PERSIST_DEBOUNCE_SECONDS:
        _persist_now()


def apagar_estado_remoto():
    """Apaga completamente o estado: nuvem + cache + sessão."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
    try:
        # 1️⃣ Apaga remoto
        requests.delete(url, headers=headers, timeout=10)

        # 2️⃣ Apaga cache local
        if os.path.exists(LOCAL_STATE_FILE):
            os.remove(LOCAL_STATE_FILE)

        # 3️⃣ Limpa a memória da sessão
        st.session_state.clear()

        # 4️⃣ Recria estrutura limpa
        inicializar_estado()

        # 5️⃣ Salva estado limpo na nuvem
        salvar_estado_duravel(force=True)

        st.sidebar.success("✅ Estado totalmente apagado (nuvem + cache + sessão).")
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado remoto: {e}")


# -----------------------------
# NOTIFICAÇÕES / TESTES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = remetente, destinatario, assunto
    if "<html" in corpo.lower():
        msg.attach(MIMEText(corpo, "html"))
    else:
        msg.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(remetente, senha_ou_token)
        s.send_message(msg)


def formatar_mensagem_alerta(ticker, preco_alvo, preco_atual, operacao):
    ticker_simples = ticker.replace(".SA", "")
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

if st.sidebar.button("🧹 Limpar Tabela"):
    apagar_estado_remoto()
    st.rerun()

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")

if st.sidebar.button("📩 Testar mensagem"):
    st.sidebar.info("Gerando alerta simulado...")
    try:
        tkr = "PETR4.SA"
        preco_alvo = 37.50
        preco_atual = 37.52
        oper = "compra"
        msg_tg, msg_email = formatar_mensagem_alerta(tkr, preco_alvo, preco_atual, oper)
        enviar_notificacao_curto(
            st.secrets.get("email_recipient_curto", ""),
            f"ALERTA CURTO PRAZO: {oper.upper()} em {tkr.replace('.SA','')}",
            msg_email,
            st.secrets.get("email_sender", ""),
            st.secrets.get("gmail_app_password", ""),
            st.secrets.get("telegram_token", ""),
            st.secrets.get("telegram_chat_id_curto", ""),
            msg_tg
        )
        st.sidebar.success("✅ Mensagem de teste enviada.")
    except Exception as e:
        st.sidebar.error(f"Erro: {e}")

if st.sidebar.button("🧹 Limpar Histórico"):
    st.session_state["historico_alertas"] = []
    salvar_estado_duravel(force=True)
    st.sidebar.success("Histórico limpo!")

if st.sidebar.button("🧹 Limpar Log de Monitoramento"):
    st.session_state["log_monitoramento"] = []
    salvar_estado_duravel(force=True)
    st.sidebar.success("Log limpo!")

if st.sidebar.button("🧹 Limpar Gráfico ⭐"):
    st.session_state["precos_historicos"] = {}
    st.session_state["disparos"] = {}
    salvar_estado_duravel(force=True)
    st.sidebar.success("Gráfico limpo!")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CURTO PRAZO - COMPRA E VENDA")
origem = st.session_state.get("origem_estado", "❓")
st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — Origem: {origem}")

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
    if ticker:
        novos = st.session_state.get("ativos", [])
        if not any(a["ticker"] == ticker for a in novos):
            novos.append({"ticker": ticker, "operacao": operacao, "preco": float(preco)})
            st.session_state["ativos"] = novos
            salvar_estado_duravel(force=True)
            st.success(f"{ticker} enviado à nuvem.")
        else:
            st.warning("Ticker já adicionado.")

# -----------------------------
# STATUS DOS ATIVOS
# -----------------------------
st.subheader("📊 Status dos Ativos (Nuvem)")

data = []
for ativo in st.session_state.get("ativos", []):
    t = ativo["ticker"]
    raw = str(st.session_state.get("status", {}).get(t, "")).lower()
    status_fmt = {
        "monitorando": "🟢 Monitorando",
        "em_contagem": "🟡 Em contagem",
        "disparado": "🚀 Disparado"
    }.get(raw, "⏳ Aguardando robô da nuvem")
    data.append({
        "Ticker": t,
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
fig = go.Figure()
for t, dados in st.session_state.get("precos_historicos", {}).items():
    if dados:
        xs, ys = [], []
        for dtv, pv in dados:
            try:
                xs.append(datetime.datetime.fromisoformat(dtv) if isinstance(dtv, str) else dtv)
            except Exception:
                xs.append(dtv)
            ys.append(pv)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t))
fig.update_layout(template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG
# -----------------------------
st.subheader("🕒 Monitoramento (Robô da Nuvem)")
log = st.session_state.get("log_monitoramento", [])
if log:
    for l in log[-300:][::-1]:
        st.text(l)
else:
    st.info("Sem entradas do robô da nuvem ainda.")

# -----------------------------
# HISTÓRICO
# -----------------------------
st.subheader("📜 Histórico de Alertas")
if st.session_state.get("historico_alertas"):
    for alerta in reversed(st.session_state["historico_alertas"]):
        st.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.info("Nenhum alerta registrado.")

# -----------------------------
# AUTOREFRESH
# -----------------------------
st_autorefresh(interval=60_000, limit=None, key="curto-refresh")
