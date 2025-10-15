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
import uuid
import streamlit.components.v1 as components
import json
import os
from streamlit_autorefresh import st_autorefresh
import time

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="🛑 LOSS CURTÍSSIMO - ENCERRAMENTO POR STOP", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(5, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(23, 0, 0)

INTERVALO_VERIFICACAO = 60
TEMPO_ACUMULADO_MAXIMO = 180  # 15 minutos na zona para encerrar
LOG_MAX_LINHAS = 1000
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# =============================
# PERSISTÊNCIA (SUPABASE via REST API + LOCAL JSON)
# =============================
SUPABASE_URL = st.secrets["supabase_url_lc"]
SUPABASE_KEY = st.secrets["supabase_key_lc"]
TABLE = "kv_state_losscurtissimo"
STATE_KEY = "losscurtissimo_przo_v1"
LOCAL_STATE_FILE = "session_data/state_losscurtissimo.json"


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

    precos_historicos_serial = {}
    for ticker, dados in st.session_state.get("precos_historicos", {}).items():
        precos_historicos_serial[ticker] = [
            (dt.isoformat() if isinstance(dt, datetime.datetime) else dt, preco)
            for dt, preco in dados
        ]
    snapshot["precos_historicos"] = precos_historicos_serial

    disparos_serial = {}
    for ticker, pontos in st.session_state.get("disparos", {}).items():
        disparos_serial[ticker] = [
            (dt.isoformat() if isinstance(dt, datetime.datetime) else dt, preco)
            for dt, preco in pontos
        ]
    snapshot["disparos"] = disparos_serial
    return snapshot

def _persist_now():
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
        if r.status_code not in (200, 201, 204):
            st.sidebar.error(f"Erro ao salvar estado remoto: {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado remoto: {e}")

    try:
        os.makedirs("session_data", exist_ok=True)
        with open(LOCAL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao salvar local: {e}")

    st.session_state["__last_save_ts"] = agora_lx().timestamp()

def salvar_estado_duravel(force: bool = False):
    if force:
        _persist_now()
        return
    last = st.session_state.get("__last_save_ts")
    now_ts = agora_lx().timestamp()
    if not last or (now_ts - last) >= PERSIST_DEBOUNCE_SECONDS:
        _persist_now()

def carregar_estado_duravel():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    remoto_ok = False
    origem = "❌ Nenhum"

    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            for k, v in estado.items():
                if k == "precos_historicos":
                    precos_reconv = {}
                    for t, dados in v.items():
                        reconv = [(datetime.datetime.fromisoformat(dt) if isinstance(dt, str) else dt, p) for dt, p in dados]
                        precos_reconv[t] = reconv
                    st.session_state[k] = precos_reconv
                elif k == "disparos":
                    disparos_reconv = {}
                    for t, pontos in v.items():
                        reconv = [(datetime.datetime.fromisoformat(pt) if isinstance(pt, str) else pt, p) for pt, p in pontos]
                        disparos_reconv[t] = reconv
                    st.session_state[k] = disparos_reconv
                else:
                    st.session_state[k] = v
            st.sidebar.info("Conectado na nuvem!")
            remoto_ok = True
            origem = "☁️ Supabase"
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto ainda.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")

    if not remoto_ok and os.path.exists(LOCAL_STATE_FILE):
        try:
            with open(LOCAL_STATE_FILE, "r", encoding="utf-8") as f:
                estado = json.load(f)
            for k, v in estado.items():
                if k == "precos_historicos":
                    precos_reconv = {}
                    for t, dados in v.items():
                        reconv = [(datetime.datetime.fromisoformat(dt) if isinstance(dt, str) else dt, p) for dt, p in dados]
                        precos_reconv[t] = reconv
                    st.session_state[k] = precos_reconv
                elif k == "disparos":
                    disparos_reconv = {}
                    for t, pontos in v.items():
                        reconv = [(datetime.datetime.fromisoformat(pt) if isinstance(pt, str) else pt, p) for pt, p in pontos]
                        disparos_reconv[t] = reconv
                    st.session_state[k] = disparos_reconv
                else:
                    st.session_state[k] = v
            st.sidebar.info("💾 Estado carregado do local (fallback)!")
            origem = "📁 Local"
        except Exception as e:
            st.sidebar.error(f"Erro no fallback local: {e}")

    # 🔧 Consistência pós-carregamento (se havia tempo acumulado sem timestamp)
    for t in st.session_state.get("tempo_acumulado", {}):
        if st.session_state.tempo_acumulado.get(t, 0) > 0 and not st.session_state.ultimo_update_tempo.get(t):
            st.session_state.ultimo_update_tempo[t] = agora_lx().isoformat()

    st.session_state["origem_estado"] = origem
    st.session_state["__carregado_ok__"] = (origem in ("☁️ Supabase", "📁 Local"))


def apagar_estado_remoto():
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}"
    try:
        r = requests.delete(url, headers=headers, timeout=15)
        if r.status_code not in (200, 204):
            st.sidebar.error(f"Erro ao apagar estado remoto: {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado remoto: {e}")

    if os.path.exists(LOCAL_STATE_FILE):
        try:
            os.remove(LOCAL_STATE_FILE)
        except Exception as e:
            st.sidebar.warning(f"⚠️ Erro ao apagar local: {e}")

def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def inicializar_estado():
    defaults = {
        "ativos": [], "historico_alertas": [], "log_monitoramento": [],
        "tempo_acumulado": {}, "em_contagem": {}, "status": {},
        "precos_historicos": {}, "ultimo_update_tempo": {},
        "pausado": False, "ultimo_estado_pausa": None,
        "disparos": {}, "__last_save_ts": None,
        "__carregado_ok__": False, "ultima_data_abertura_enviada": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    ensure_color_map()

inicializar_estado()
carregar_estado_duravel()
# Passo 1: garantir que exista
if "eventos_enviados" not in st.session_state:
    st.session_state["eventos_enviados"] = {}
# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = remetente, destinatario, assunto
    msg.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(remetente, senha_ou_token)
        s.send_message(msg)

def enviar_notificacao_curto(dest, assunto, corpo_email_html, rem, senha, tok_tg, chat_id, corpo_telegram=None):
    """
    Envia e-mail em HTML e mensagem Telegram (HTML), com compatibilidade retroativa.
    """
    # --- E-mail (em HTML) ---
    if senha and dest:
        try:
            mensagem = MIMEMultipart()
            mensagem["From"] = rem
            mensagem["To"] = dest
            mensagem["Subject"] = assunto

            if "<html" in corpo_email_html.lower():
                mensagem.attach(MIMEText(corpo_email_html, "html"))
            else:
                mensagem.attach(MIMEText(corpo_email_html, "plain"))

            with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
                servidor.starttls()
                servidor.login(rem, senha)
                servidor.send_message(mensagem)

            st.session_state.log_monitoramento.append("📧 E-mail enviado com sucesso.")
        except Exception as e:
            st.session_state.log_monitoramento.append(f"⚠️ Erro e-mail: {e}")
    else:
        st.session_state.log_monitoramento.append("⚠️ Email não configurado.")

    # --- Telegram (HTML ou texto simples) ---
    async def send_tg():
        try:
            if tok_tg and chat_id:
                bot = Bot(token=tok_tg)
                texto_final = corpo_telegram if corpo_telegram else corpo_email_html
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"{texto_final}\n\n🤖 Robô 1milhão Invest",
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        except Exception as e:
            st.session_state.log_monitoramento.append(f"⚠️ Erro Telegram: {e}")

    asyncio.run(send_tg())


@st.cache_data(ttl=5)
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
    preco_atual = tk.history(period="1d")["close"].iloc[-1]
    return float(preco_atual)

# -----------------------------
# MENSAGENS DE ENCERRAMENTO (LOSS CURTÍSSIMO)
# -----------------------------
# -----------------------------
# MENSAGENS DE ENCERRAMENTO (LOSS CURTÍSSIMO)
# -----------------------------
def formatar_mensagem_encerramento(ticker_symbol, preco_alvo, preco_atual, operacao):
    """
    Gera o texto formatado de ENCERRAMENTO (STOP) para Telegram e E-mail
    com racional de STOP (oposto ao de entrada): 
      - se operacao == "venda"  → posição anterior era COMPRA, gatilho: preço ≤ STOP
      - se operacao == "compra" → posição anterior era VENDA A DESCOBERTO, gatilho: preço ≥ STOP
    """
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")

    # operação anterior (a que está sendo encerrada) e condição de stop
    if operacao == "venda":
        msg_operacao_anterior = "COMPRA"
        condicao_txt = "preço ≤ STOP"
        direcao = "⬇️ Queda"
        detalhe_val = "≤" if (preco_atual is not None and preco_alvo is not None and preco_atual <= preco_alvo) else ">"
    else:
        msg_operacao_anterior = "VENDA A DESCOBERTO"
        condicao_txt = "preço ≥ STOP"
        direcao = "⬆️ Alta"
        detalhe_val = "≥" if (preco_atual is not None and preco_alvo is not None and preco_atual >= preco_alvo) else "<"

    msg_operacao_encerrar = operacao.upper()
    detalhe_num = (
        f"(atual R$ {preco_atual:.2f} {detalhe_val} STOP R$ {preco_alvo:.2f})"
        if (preco_atual is not None and preco_alvo is not None) else ""
    )

    # --- Texto para Telegram (HTML) — formato seguro (sem triple-quote)
    mensagem_telegram = (
        f"🛑 <b>ENCERRAMENTO (STOP) ATIVADO!</b>\n\n"
        f"<b>Ticker:</b> {ticker_symbol_sem_ext}\n"
        f"<b>Operação anterior:</b> {msg_operacao_anterior}\n"
        f"<b>Operação para encerrar:</b> {msg_operacao_encerrar}\n"
        f"<b>STOP (alvo):</b> R$ {preco_alvo:.2f}\n"
        f"<b>Preço atual:</b> R$ {preco_atual:.2f}\n\n"
        f"📊 <a href=\"https://br.tradingview.com/symbols/{ticker_symbol_sem_ext}\">Abrir gráfico no TradingView</a>\n\n"
        f"<em>"
        f"COMPLIANCE: Esta mensagem é uma sugestão de ENCERRAMENTO baseada na CARTEIRA CURTÍSSIMO PRAZO. "
        f"A execução é de total decisão e responsabilidade do Destinatário. "
        f"Esta informação é CONFIDENCIAL, de propriedade de 1milhao Invest e de seu DESTINATÁRIO tão somente. "
        f"Se você NÃO for DESTINATÁRIO ou pessoa autorizada a recebê-lo, NÃO PODE usar, copiar, transmitir, retransmitir "
        f"ou divulgar seu conteúdo (no todo ou em partes), estando sujeito às penalidades da LEI. "
        f"A Lista de Ações do 1milhao Invest é devidamente REGISTRADA."
        f"</em>"
    ).strip()

    # --- Corpo HTML do e-mail (dark, título vermelho, compliance menor/cinza) ---
    corpo_email_html = f"""
<html>
  <body style="font-family:Arial,sans-serif; background-color:#0b1220; color:#e5e7eb; padding:20px;">
    <h2 style="color:#ef4444;">ALERTA STOP CARTEIRA CURTISSIMO PRAZO</h2>
    <p><b>Ticker:</b> {ticker_symbol_sem_ext}</p>
    <p><b>Operação anterior:</b> {msg_operacao_anterior}</p>
    <p><b>Operação para encerrar:</b> {msg_operacao_encerrar}</p>
    <p><b>STOP (alvo):</b> R$ {preco_alvo:.2f}</p>
    <p><b>Preço atual:</b> R$ {preco_atual:.2f}</p>    
    <p>📊 <a href="https://br.tradingview.com/symbols/{ticker_symbol_sem_ext}" style="color:#60a5fa;">Ver gráfico no TradingView</a></p>
    <hr style="border:1px solid #ef4444; margin:20px 0;">
    <p style="font-size:11px; line-height:1.4; color:#9ca3af;">
      <b>COMPLIANCE:</b> Esta mensagem é uma sugestão de ENCERRAMENTO baseada na CARTEIRA CURTÍSSIMO PRAZO.<br>
      A execução é de total decisão e responsabilidade do Destinatário.<br>
      Esta informação é <b>CONFIDENCIAL</b>, de propriedade do Canal 1milhao e de seu DESTINATÁRIO tão somente.<br>
      Se você <b>NÃO</b> for DESTINATÁRIO ou pessoa autorizada a recebê-lo, <b>NÃO PODE</b> usar, copiar, transmitir, retransmitir
      ou divulgar seu conteúdo (no todo ou em partes), estando sujeito às penalidades da LEI.<br>
      A Lista de Ações do Canal 1milhao é devidamente <b>REGISTRADA.</b>
    </p>
  </body>
</html>
""".strip()

    return mensagem_telegram, corpo_email_html



def notificar_preco_alvo_alcancado_loss(ticker, preco_alvo, preco_atual, operacao):
    """
    Envia mensagens de ENCERRAMENTO (STOP) no mesmo padrão visual do curtíssimo.
    """
    msg_telegram, msg_email_html = formatar_mensagem_encerramento(ticker, preco_alvo, preco_atual, operacao)
    tk_sem_ext = ticker.replace(".SA", "")
    assunto = f"🛑 ENCERRAMENTO (STOP) — {tk_sem_ext}"

    remetente = st.secrets.get("email_sender", "")
    senha = st.secrets.get("gmail_app_password", "")
    destinatario = st.secrets.get("email_recipient_losscurtissimo", "")
    token_tg = st.secrets.get("telegram_token", "")
    chat_id = st.secrets.get("telegram_chat_id_losscurtissimo", "")

    try:
        enviar_notificacao_curto(destinatario, assunto, msg_email_html, remetente, senha, token_tg, chat_id, msg_telegram)
        st.session_state.log_monitoramento.append(f"📤 Encerramento enviado: {tk_sem_ext}")
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ Erro no envio de encerramento: {e}")

    return f"🛑 ENCERRAMENTO (STOP) de {tk_sem_ext} enviado com sucesso!"

# -----------------------------
# TESTES / ABERTURA DE PREGÃO
# -----------------------------
async def testar_telegram():
    tok = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_losscurtissimo", "")
    try:
        if tok and chat:
            bot = Bot(token=tok)
            await bot.send_message(chat_id=chat, text="✅ Teste de ENCERRAMENTO (LOSS CURTÍSSIMO) funcionando!")
            return True, None
        return False, "token/chat_id não configurado"
    except Exception as e:
        return False, str(e)

def dentro_pregao(dt):
    t = dt.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_abertura(dt):
    abre = dt.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=0, second=0, microsecond=0)
    fecha = dt.replace(hour=HORARIO_FIM_PREGAO.hour, minute=0, second=0, microsecond=0)
    if dt < abre:
        return int((abre - dt).total_seconds()), abre
    elif dt > fecha:
        prox = abre + datetime.timedelta(days=1)
        return int((prox - dt).total_seconds()), prox
    else:
        return 0, abre

def notificar_abertura_pregao_uma_vez_por_dia():
    """Envia notificação de pregão aberto no máximo uma vez por dia (LOSS)."""
    now = agora_lx()
    data_atual = now.date()
    ultima_data_envio = st.session_state.get("ultima_data_abertura_enviada")

    if ultima_data_envio == str(data_atual):
        return

    try:
        tok = st.secrets.get("telegram_token", "").strip()
        chat = st.secrets.get("telegram_chat_id_losscurtissimo", "").strip()
        if tok and chat:
            bot = Bot(token=tok)
            asyncio.run(bot.send_message(chat_id=chat, text="🛑 Robô LOSS CURTÍSSIMO ativo — Pregão Aberto!"))
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | 📣 Telegram: Pregão Aberto (LOSS)")
        else:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Telegram não configurado (LOSS).")
    except Exception as e:
        st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Erro Telegram (LOSS): {e}")

    st.session_state["ultima_data_abertura_enviada"] = str(data_atual)
    salvar_estado_duravel(force=True)

# -----------------------------
# INTERFACE E SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("🧹 Limpar Tabela"):
    try:
        apagar_estado_remoto()
        try:
            if os.path.exists(LOCAL_STATE_FILE):
                os.remove(LOCAL_STATE_FILE)
        except Exception as e_local:
            st.sidebar.warning(f"⚠️ Erro ao apagar arquivo local: {e_local}")

        st.session_state.clear()
        inicializar_estado()
        if "eventos_enviados" not in st.session_state:
            st.session_state.eventos_enviados = {}
        st.session_state["ultima_data_abertura_enviada"] = str(agora_lx().date())
        st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | 🧹 Reset manual do estado (LOSS)")
        salvar_estado_duravel(force=True)
        st.sidebar.success("✅ Estado apagado e reiniciado (sem alerta de pregão aberto).")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste (LOSS)...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!") if ok else st.sidebar.error(f"❌ Falha: {erro}")

if st.sidebar.button("📩 Testar mensagem de ENCERRAMENTO"):
    st.sidebar.info("Gerando ENCERRAMENTO simulado...")
    ticker_teste = "PETR4.SA"
    preco_alvo = 37.50
    preco_atual = 37.52
    operacao = "venda"  # exemplo: encerrar com venda (se anterior foi compra)
    try:
        msg = notificar_preco_alvo_alcancado_loss(ticker_teste, preco_alvo, preco_atual, operacao)
        st.sidebar.success("✅ Mensagem de ENCERRAMENTO enviada (verifique Telegram e e-mail).")
        st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | 🧪 Teste ENCERRAMENTO executado com sucesso.")
    except Exception as e:
        st.sidebar.error(f"❌ Erro no teste: {e}")
        st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | ⚠️ Erro teste ENCERRAMENTO: {e}")

st.sidebar.checkbox("⏸️ Pausar monitoramento", key="pausado")
salvar_estado_duravel()

st.sidebar.header("📜 Histórico de Encerramentos")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | STOP: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum encerramento ainda.")

if st.sidebar.button("🧹 Limpar Histórico"):
    st.session_state.historico_alertas.clear()
    salvar_estado_duravel(force=True)
    st.sidebar.success("Histórico limpo!")
if st.sidebar.button("🧹 Limpar Monitoramento"):
    st.session_state.log_monitoramento.clear()
    salvar_estado_duravel(force=True)
    st.sidebar.success("Log limpo!")

if st.sidebar.button("🧹 Limpar Gráfico ⭐"):
    st.session_state.disparos = {}
    ativos_atuais = {a["ticker"] for a in st.session_state.ativos}
    st.session_state.precos_historicos = {
        t: dados for t, dados in st.session_state.precos_historicos.items() if t in ativos_atuais
    }
    st.session_state.tempo_acumulado = {
        t: v for t, v in st.session_state.tempo_acumulado.items() if t in ativos_atuais
    }
    st.session_state.em_contagem = {
        t: v for t, v in st.session_state.em_contagem.items() if t in ativos_atuais
    }
    st.session_state.status = {
        t: v for t, v in st.session_state.status.items() if t in ativos_atuais
    }
    salvar_estado_duravel(force=True)
    st.sidebar.success("Marcadores e históricos antigos limpos!")

tickers_existentes = sorted(set(a["ticker"] for a in st.session_state.ativos)) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("🛑 LOSS CURTÍSSIMO - ENCERRAMENTO POR STOP")

origem = st.session_state.get("origem_estado", "❓")
st.markdown({
    "☁️ Supabase": "🟢 **Origem dos dados:** Nuvem (Supabase)",
    "📁 Local": "🟠 **Origem dos dados:** Local",
}.get(origem, "⚪ **Origem dos dados:** Desconhecida"))

st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")
st.write("Robô automático da **CARTEIRA CURTÍSSIMO PRAZO (LOSS)** — envia **ENCERRAMENTO (STOP)** após **15 min** na zona de preço alvo.")

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Ação para encerrar posição", ["compra", "venda"])
with col3:
    preco = st.number_input("STOP (preço alvo)", min_value=0.01, step=0.01)

if st.button("➕ Adicionar STOP"):
    if not ticker:
        st.error("Digite um ticker válido.")
    else:
        ativo = {"ticker": ticker, "operacao": operacao, "preco": preco}
        st.session_state.ativos.append(ativo)
        st.session_state.tempo_acumulado[ticker] = 0
        st.session_state.em_contagem[ticker] = False
        st.session_state.status[ticker] = "🟢 Monitorando"
        st.session_state.precos_historicos[ticker] = []
        st.session_state.ultimo_update_tempo[ticker] = None
        try:
            preco_inicial = obter_preco_atual(f"{ticker}.SA")
            if preco_inicial != "-":
                st.session_state.precos_historicos[ticker].append((now, preco_inicial))
                time.sleep(1)
                preco_seg = obter_preco_atual(f"{ticker}.SA")
                if preco_seg != "-":
                    st.session_state.precos_historicos[ticker].append((agora_lx(), preco_seg))
                st.success(f"STOP de {ticker} adicionado e gráfico inicializado.")
            else:
                st.warning(f"STOP de {ticker} adicionado, sem preço inicial.")
        except Exception as e:
            st.error(f"Erro ao coletar preço de {ticker}: {e}")
        salvar_estado_duravel(force=True)

# -----------------------------
# STATUS + GRÁFICO + LOG
# -----------------------------
st.subheader("📊 Status dos STOPs Monitorados")
tabela_status = st.empty()
grafico = st.empty()
st.subheader("🕒 Monitoramento")
log_container = st.empty()

# -----------------------------
# LOOP DE MONITORAMENTO (LOSS)
# -----------------------------
sleep_segundos = 60
if st.session_state.pausado:
    st.info("⏸️ Monitoramento pausado.")
else:
    now = agora_lx()
    # 🧩 Exibe a tabela mesmo fora do pregão (mantém última atualização)
    if st.session_state.ativos:
        data = []
        now = agora_lx()
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_alvo = ativo["preco"]
            operacao_lbl = ativo["operacao"].upper()
            tempo = st.session_state.tempo_acumulado.get(t, 0)
            minutos = tempo / 60
            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception:
                pass

            data.append({
                "Ticker": t,
                "Ação para encerrar": operacao_lbl,
                "STOP (alvo)": f"R$ {preco_alvo:.2f}",
                "Preço Atual": f"R$ {preco_atual:.2f}" if preco_atual != "-" else "-",
                "Status": st.session_state.status.get(t, "🟢 Monitorando"),
                "Tempo Acumulado": f"{int(minutos)} min"
            })

        tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)
    else:
        tabela_status.info("Nenhum ativo monitorado no momento.")

    if dentro_pregao(now):
        notificar_abertura_pregao_uma_vez_por_dia()
        data = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro {t}: {e}")
            if preco_atual != "-":
                st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))

            tempo = st.session_state.tempo_acumulado.get(t, 0)
            minutos = tempo / 60
            data.append({
                "Ticker": t,
                "Ação para encerrar": ativo["operacao"].upper(),
                "STOP (alvo)": f"R$ {ativo['preco']:.2f}",
                "Preço Atual": f"R$ {preco_atual:.2f}" if preco_atual != "-" else "-",
                "Status": st.session_state.status.get(t, "🟢 Monitorando"),
                "Tempo Acumulado": f"{int(minutos)} min"
            })
        if data:
            tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)

        tickers_para_remover = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_alvo = ativo["preco"]
            operacao_atv = ativo["operacao"]  # "compra" ou "venda"
            tk_full = f"{t}.SA"

            try:
                preco_atual = obter_preco_atual(tk_full)
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro {t}: {e}")
                continue

            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | {tk_full}: R$ {preco_atual:.2f}")

            # Mantemos a mesma regra de "zona" do curtíssimo:
            # compra: preço_atual >= alvo  |  venda: preço_atual <= alvo
            condicao = (
                (operacao_atv == "compra" and preco_atual >= preco_alvo) or
                (operacao_atv == "venda" and preco_atual <= preco_alvo)
            )

            # -----------------------------
            # BLOCO PRINCIPAL DE CONTAGEM (LOSS)
            # -----------------------------
            if condicao:
                st.session_state.status[t] = "🟡 Em contagem"

                if not st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = True
                    if not st.session_state.ultimo_update_tempo.get(t) and st.session_state.tempo_acumulado.get(t, 0) == 0:
                        st.session_state.tempo_acumulado[t] = 0
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⚠️ {t} entrou no STOP ({preco_alvo:.2f}). Iniciando/retomando contagem..."
                    )
                    salvar_estado_duravel(force=True)

                else:
                    ultimo = st.session_state.ultimo_update_tempo.get(t)
                    st.session_state.log_monitoramento.append(
                        f"🐞 DEBUG {t}: ultimo_update_tempo bruto = {ultimo}"
                    )

                    if ultimo:
                        try:
                            if isinstance(ultimo, str):
                                dt_ultimo = datetime.datetime.fromisoformat(ultimo)
                                if dt_ultimo.tzinfo is None:
                                    dt_ultimo = dt_ultimo.replace(tzinfo=TZ)
                            else:
                                dt_ultimo = ultimo
                        except Exception as e:
                            st.session_state.log_monitoramento.append(
                                f"🐞 DEBUG {t}: erro convertendo ultimo_update_tempo → {e}"
                            )
                            dt_ultimo = now
                    else:
                        dt_ultimo = now

                    delta = (now - dt_ultimo).total_seconds()
                    if delta < 0:
                        delta = 0

                    st.session_state.tempo_acumulado[t] = float(st.session_state.tempo_acumulado.get(t, 0)) + float(delta)
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()

                    st.session_state.log_monitoramento.append(
                        f"⌛ {t}: {int(st.session_state.tempo_acumulado[t])}s acumulados (+{int(delta)}s)"
                    )

                    salvar_estado_duravel(force=True)

                # 🚀 Disparo de ENCERRAMENTO quando atinge o tempo máximo (900s)
                if (
                    st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO
                    and st.session_state.status.get(t) != "🚀 Encerrado"
                ):
                    # id simples por dia (ticker + ação + alvo + data)
                    event_id = f"{t}|{operacao_atv}|{preco_alvo:.2f}|{now.date()}"
                
                    # já enviou? evita duplicidade
                    if st.session_state.get("eventos_enviados", {}).get(event_id):
                        st.session_state.log_monitoramento.append(
                            f"🔁 {t}: envio ignorado (já enviado)."
                        )
                    else:
                        # marque como encerrado e PERSISTA antes de enviar (corta duplicado por re-run)
                        st.session_state.status[t] = "🚀 Encerrado"
                        st.session_state.setdefault("eventos_enviados", {})[event_id] = True
                        salvar_estado_duravel(force=True)
                
                        try:
                            alerta_msg = notificar_preco_alvo_alcancado_loss(tk_full, preco_alvo, preco_atual, operacao_atv)
                            st.warning(alerta_msg)
                
                            st.session_state.historico_alertas.append({
                                "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                                "ticker": t,
                                "operacao": operacao_atv,
                                "preco_alvo": preco_alvo,
                                "preco_atual": preco_atual
                            })
                            st.session_state.disparos.setdefault(t, []).append((now, preco_atual))
                        except Exception as e:
                            st.session_state.log_monitoramento.append(f"⚠️ Erro no envio de encerramento: {e}")
                        finally:
                            salvar_estado_duravel(force=True)
                
                    tickers_para_remover.append(t)


            else:
                if st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🔴 Fora do STOP"
                    st.session_state.ultimo_update_tempo[t] = None
                    st.session_state.log_monitoramento.append(f"❌ {t} saiu da zona de STOP.")
                    salvar_estado_duravel(force=True)

        if tickers_para_remover:
            st.session_state.ativos = [a for a in st.session_state.ativos if a["ticker"] not in tickers_para_remover]
            for t in tickers_para_remover:
                st.session_state.tempo_acumulado.pop(t, None)
                st.session_state.em_contagem.pop(t, None)
                st.session_state.status[t] = "✅ Encerrado (removido)"
                st.session_state.ultimo_update_tempo.pop(t, None)
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | 🧹 Removidos após ENCERRAMENTO: {', '.join(tickers_para_remover)}"
            )
            salvar_estado_duravel(force=True)
        sleep_segundos = INTERVALO_VERIFICACAO
    else:
        st.session_state["avisou_abertura_pregao"] = False
        faltam, prox_abertura = segundos_ate_abertura(now)
        components.html(f"""
        <div style="background:#0b1220;border:1px solid #1f2937;
             border-radius:10px;padding:12px;margin-top:10px;
             color:white;">
            ⏸️ Pregão fechado. Reabre em 
            <b style="color:#60a5fa;">{datetime.timedelta(seconds=faltam)}</b>
            (às <span style="color:#60a5fa;">{prox_abertura.strftime('%H:%M')}</span>).
        </div>""", height=70)

        try:
            APP_URL = "https://losscurtissimo.streamlit.app"
            ultimo_ping = st.session_state.get("ultimo_ping_keepalive")
            if isinstance(ultimo_ping, str):
                ultimo_ping = datetime.datetime.fromisoformat(ultimo_ping)
            if not ultimo_ping or (now - ultimo_ping).total_seconds() > 900:
                requests.get(APP_URL, timeout=5)
                st.session_state["ultimo_ping_keepalive"] = now.isoformat()
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | 🔄 Hibernado e aguardando próximo pregão")
                salvar_estado_duravel()
        except Exception as e:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Erro keep-alive: {e}")
        sleep_segundos = 300

# -----------------------------
# GRÁFICO FINAL
# -----------------------------
fig = go.Figure()
for t, dados in st.session_state.precos_historicos.items():
    if len(dados) > 0:
        xs, ys = zip(*dados)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t,
                                 line=dict(color=st.session_state.ticker_colors.get(t, "#ef4444"), width=2)))
for t, pontos in st.session_state.disparos.items():
    if pontos:
        xs, ys = zip(*pontos)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name=f"Encerramento {t}",
                                 marker=dict(symbol="star", size=12, line=dict(width=2, color="white"))))
fig.update_layout(title="📉 Evolução dos Preços (Encerramentos ⭐)", template="plotly_dark")
grafico.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG E AUTOREFRESH
# -----------------------------
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]
    salvar_estado_duravel()

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

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

def render_log_html(lines, selected_tickers=None, max_lines=250):
    """Renderiza o log com cores, badges e rolagem."""
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]
    if selected_tickers:
        subset = [l for l in subset if extract_ticker(l) in selected_tickers]

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
      .log-line {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px;
        line-height: 1.35;
        margin: 2px 0;
        color: #e5e7eb;
        display: flex;
        align-items: baseline;
        gap: 8px;
      }
      .ts {
        color: #9ca3af;
        min-width: 64px;
        text-align: right;
      }
      .badge {
        display: inline-block;
        padding: 1px 8px;
        font-size: 12px;
        border-radius: 9999px;
        color: white;
      }
      .msg {
        white-space: pre-wrap;
      }
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

with log_container:
    render_log_html(st.session_state.log_monitoramento, selected_tickers, 250)

# -----------------------------
# DEBUG + AUTOREFRESH
# -----------------------------
with st.expander("🧪 Debug / Backup do estado (JSON)", expanded=False):
    try:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v,updated_at"
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200 and res.json():
            state_preview = res.json()[0]["v"]
            st.json(state_preview)
            st.download_button("⬇️ Baixar state_loss_curtissimo.json",
                               data=json.dumps(state_preview, indent=2),
                               file_name="state_loss_curtissimo.json", mime="application/json")
        else:
            st.info("Nenhum estado salvo ainda.")
    except Exception as e:
        st.error(f"Erro ao exibir JSON: {e}")

refresh_ms = 50_000  # atualização visual a cada 50 segundos (não afeta lógica de tempo)
st_autorefresh(interval=refresh_ms, limit=None, key="loss-curtissimo-refresh")



