# CURTO.PY - Interface Operacional (Somente Envia / Lê da Nuvem)
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
import re
import json
import os
from streamlit_autorefresh import st_autorefresh
import time

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# -----------------------------
# SUPABASE CONFIG
# -----------------------------
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"
LOCAL_STATE_FILE = "session_data/state_curto.json"  # não usamos mais para salvar, apenas limpamos se existir


def agora_lx():
    return datetime.datetime.now(TZ)


def inicializar_estado():
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
        if k not in st.session_state:
            st.session_state[k] = v


def carregar_estado_duravel():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    origem = "❌ Nenhum"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            for k, v in estado.items():
                st.session_state[k] = v
            st.sidebar.info("Conectado na nuvem!")
            origem = "☁️ Supabase"
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto ainda.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")
    st.session_state["origem_estado"] = origem
    st.session_state["__carregado_ok__"] = (origem == "☁️ Supabase")


def _persist_now():
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
        "Prefer": "resolution=replace",
    }

    payload = {"k": STATE_KEY, "v": snapshot}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?on_conflict=k"  # ✅ UPSERT automático
    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        if r.status_code not in (200, 201, 204):
            st.sidebar.error(f"Erro ao salvar estado remoto: {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado remoto: {e}")

    st.session_state["__last_save_ts"] = agora_lx().timestamp()




def salvar_estado_duravel(force: bool = False):
    if force:
        _persist_now()
        return
    last = st.session_state.get("__last_save_ts")
    now_ts = agora_lx().timestamp()
    if not last or (now_ts - last) >= PERSIST_DEBOUNCE_SECONDS:
        _persist_now()


def apagar_estado_remoto():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
    try:
        requests.delete(url, headers=headers, timeout=15)
        st.sidebar.success("✅ Estado remoto apagado com sucesso!")
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado remoto: {e}")


# -----------------------------
# NOTIFICAÇÕES (mantidas para testes manuais)
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = remetente, destinatario, assunto
    # corpo pode ser texto puro ou HTML
    if "<html" in corpo.lower():
        msg.attach(MIMEText(corpo, "html"))
    else:
        msg.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(remetente, senha_ou_token)
        s.send_message(msg)


def formatar_mensagem_alerta(ticker_symbol, preco_alvo, preco_atual, operacao):
    """Templates padronizados para teste (Telegram HTML + E-mail HTML)."""
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"

    mensagem_telegram = f"""
💥 <b>ALERTA DE {msg_op.upper()} ATIVADA!</b>\n\n
<b>Ticker:</b> {ticker_symbol_sem_ext}\n
<b>Preço alvo:</b> R$ {preco_alvo:.2f}\n
<b>Preço atual:</b> R$ {preco_atual:.2f}\n\n
📊 <a href='https://br.tradingview.com/symbols/{ticker_symbol_sem_ext}'>Abrir gráfico no TradingView</a>\n\n
<em>
COMPLIANCE: Esta mensagem é uma sugestão de compra/venda baseada em nossa CARTEIRA.
A compra ou venda é de total decisão e responsabilidade do Destinatário.
Esta informação é CONFIDENCIAL, de propriedade de 1milhao Invest e de seu DESTINATÁRIO tão somente.
Se você NÃO for DESTINATÁRIO ou pessoa autorizada a recebê-lo, NÃO PODE usar, copiar, transmitir, retransmitir
ou divulgar seu conteúdo (no todo ou em partes), estando sujeito às penalidades da LEI.
A Lista de Ações do 1milhao Invest é devidamente REGISTRADA.
</em>
""".strip()

    corpo_email_html = f"""
<html>
  <body style="font-family:Arial,sans-serif; background-color:#0b1220; color:#e5e7eb; padding:20px;">
    <h2 style="color:#3b82f6;">💥 ALERTA DE {msg_op.upper()} ATIVADA!</h2>
    <p><b>Ticker:</b> {ticker_symbol_sem_ext}</p>
    <p><b>Preço alvo:</b> R$ {preco_alvo:.2f}</p>
    <p><b>Preço atual:</b> R$ {preco_atual:.2f}</p>
    <p>📊 <a href="https://br.tradingview.com/symbols/{ticker_symbol_sem_ext}" style="color:#60a5fa;">Ver gráfico no TradingView</a></p>
    <hr style="border:1px solid #3b82f6; margin:20px 0;">
    <p style="font-size:11px; line-height:1.4; color:#9ca3af;">
      <b>COMPLIANCE:</b> Esta mensagem é uma sugestão de compra/venda baseada em nossa CARTEIRA.<br>
      A compra ou venda é de total decisão e responsabilidade do Destinatário.<br>
      Esta informação é <b>CONFIDENCIAL</b>, de propriedade do Canal 1milhao e de seu DESTINATÁRIO tão somente.<br>
      Se você <b>NÃO</b> for DESTINATÁRIO ou pessoa autorizada a recebê-lo, <b>NÃO PODE</b> usar, copiar, transmitir, retransmitir
      ou divulgar seu conteúdo (no todo ou em partes), estando sujeito às penalidades da LEI.<br>
      A Lista de Ações do Canal 1milhao é devidamente <b>REGISTRADA.</b>
    </p>
  </body>
</html>
""".strip()

    return mensagem_telegram, corpo_email_html


def enviar_notificacao_curto(dest, assunto, corpo_email_html, rem, senha, tok_tg, chat_id, corpo_telegram=None):
    """Envia e-mail (HTML) e Telegram (HTML) para teste manual."""
    # Envia e-mail
    if senha and dest and rem:
        try:
            enviar_email(dest, assunto, corpo_email_html, rem, senha)
        except Exception as e:
            st.sidebar.warning(f"⚠️ Falha ao enviar e-mail de teste: {e}")

    # Envia Telegram
    async def send_tg():
        try:
            if tok_tg and chat_id:
                bot = Bot(token=tok_tg)
                texto_final = corpo_telegram if corpo_telegram else corpo_email_html
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"{texto_final}\n\n🤖 Robot 1milhão Invest",
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        except Exception as e:
            st.sidebar.warning(f"⚠️ Falha ao enviar Telegram de teste: {e}")

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
    try:
        apagar_estado_remoto()
        if os.path.exists(LOCAL_STATE_FILE):
            try:
                os.remove(LOCAL_STATE_FILE)
            except Exception as e_local:
                st.sidebar.warning(f"⚠️ Erro ao apagar arquivo local: {e_local}")
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        inicializar_estado()
        salvar_estado_duravel(force=True)
        st.sidebar.success("✅ Todos os dados foram apagados.")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")

# TESTE COMPLETO DE ALERTA (mantido)
if st.sidebar.button("📩 Testar mensagem"):
    st.sidebar.info("Gerando alerta simulado...")
    try:
        ticker_teste = "PETR4.SA"
        preco_alvo = 37.50
        preco_atual = 37.52
        operacao = "compra"
        msg_telegram, msg_email_html = formatar_mensagem_alerta(ticker_teste, preco_alvo, preco_atual, operacao)

        remetente = st.secrets.get("email_sender", "")
        senha = st.secrets.get("gmail_app_password", "")
        destinatario = st.secrets.get("email_recipient_curto", "")
        token_tg = st.secrets.get("telegram_token", "")
        chat_id = st.secrets.get("telegram_chat_id_curto", "")
        assunto = f"ALERTA CURTO PRAZO: {operacao.upper()} em {ticker_teste.replace('.SA','')}"

        enviar_notificacao_curto(destinatario, assunto, msg_email_html, remetente, senha, token_tg, chat_id, msg_telegram)
        st.sidebar.success("✅ Mensagem de teste enviada (verifique Telegram e e-mail).")
    except Exception as e:
        st.sidebar.error(f"❌ Erro no teste: {e}")

# LIMPAR HISTÓRICO
if st.sidebar.button("🧹 Limpar Histórico"):
    st.session_state["historico_alertas"] = []
    salvar_estado_duravel(force=True)
    st.sidebar.success("Histórico limpo!")

# LIMPAR LOG MONITORAMENTO
if st.sidebar.button("🧹 Limpar Log de Monitoramento"):
    st.session_state["log_monitoramento"] = []
    salvar_estado_duravel(force=True)
    st.sidebar.success("Log limpo!")

# LIMPAR GRÁFICO
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
st.markdown({
    "☁️ Supabase": "🟢 **Origem dos dados:** Nuvem (Supabase)",
    "📁 Local": "🟠 **Origem dos dados:** Local",
}.get(origem, "⚪ **Origem dos dados:** Desconhecida"))

st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

# Adicionar Ativo
col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
    if ticker:
        novo = {"ticker": ticker, "operacao": operacao, "preco": float(preco)}
        atuais = st.session_state.get("ativos", [])
        # evita duplicata exata por ticker (ajuste se quiser por (ticker, operacao, preco))
        if not any(a["ticker"] == ticker for a in atuais):
            atuais.append(novo)
            st.session_state["ativos"] = atuais
            salvar_estado_duravel(force=True)
            st.success(f"{ticker} enviado para a nuvem.")
        else:
            st.warning("Esse ativo já está na lista.")

# -----------------------------
# STATUS DOS ATIVOS (mapeando para ícones)
# -----------------------------
st.subheader("📊 Status dos Ativos (Nuvem)")

data = []
for ativo in st.session_state.get("ativos", []):
    t = ativo["ticker"]
    status_raw = st.session_state.get("status", {}).get(t, "")
    status_display = {
        "monitorando": "🟢 Monitorando",
        "em_contagem": "🟡 Em contagem",
        "disparado": "🚀 Disparado"
    }.get(str(status_raw).lower(), "—")

    data.append({
        "Ticker": t,
        "Operação": ativo["operacao"].upper(),
        "Preço Alvo": f"R$ {float(ativo['preco']):.2f}",
        "Status": status_display
    })

if data:
    st.dataframe(pd.DataFrame(data), use_container_width=True, height=250)
else:
    st.info("Nenhum ativo cadastrado.")

# -----------------------------
# GRÁFICO (somente da nuvem)
# -----------------------------
st.subheader("📈 Evolução dos Preços (Robô da Nuvem)")
fig = go.Figure()
for t, dados in st.session_state.get("precos_historicos", {}).items():
    if dados:
        xs, ys = [], []
        for dtv, pv in dados:
            if isinstance(dtv, str):
                try:
                    xs.append(datetime.datetime.fromisoformat(dtv))
                except Exception:
                    xs.append(dtv)
            else:
                xs.append(dtv)
            ys.append(pv)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t))
fig.update_layout(template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG DE MONITORAMENTO (Nuvem)
# -----------------------------
st.subheader("🕒 Monitoramento (Robô da Nuvem)")
log_lines = st.session_state.get("log_monitoramento", [])
if log_lines:
    for l in log_lines[-300:][::-1]:
        st.text(l)
else:
    st.info("Sem entradas do robô da nuvem ainda.")

# -----------------------------
# HISTÓRICO DE ALERTAS
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
