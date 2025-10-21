# CURTO.PY - ENVIO DE ORDENS


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
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(9, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(23, 0, 0)

INTERVALO_VERIFICACAO = 300
TEMPO_ACUMULADO_MAXIMO = 1500
LOG_MAX_LINHAS = 1000
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]


# PERSISTÊNCIA (SUPABASE via REST API + LOCAL JSON)
# =============================
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"

# --- controle de chaves ---
STATE_KEY_LOCAL = "curto_przo_v1_local"   # linha da interface (PC)
STATE_KEY_CLOUD = "curto_przo_v1"         # linha dos robôs na nuvem (já existente)
STATE_KEY = STATE_KEY_LOCAL               # esta instância usa sempre a linha local
REMOTE_KEYS = [STATE_KEY_LOCAL, STATE_KEY_CLOUD]

LOCAL_STATE_FILE = "session_data/state_curto.json"  # fallback local
PERSIST_DEBOUNCE_SECONDS = 60


def agora_lx():
    return datetime.datetime.now(TZ)


# -----------------------------------------------------
#  garantir que a linha local existe no Supabase
# -----------------------------------------------------
def garantir_estado_local_existe():
    """
    Garante que a linha local (_local) exista no Supabase.
    Se não existir, clona o snapshot da linha da nuvem (curto_przo_v1).
    """
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        # Verifica se a linha local já existe
        url_check = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_LOCAL}&select=k"
        r_check = requests.get(url_check, headers=headers, timeout=10)
        if r_check.status_code == 200 and r_check.json():
            return  # Já existe

        # Se não existe, tenta copiar o estado da linha dos robôs
        url_src = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_CLOUD}&select=v"
        r_src = requests.get(url_src, headers=headers, timeout=10)
        snapshot = r_src.json()[0]["v"] if (r_src.status_code == 200 and r_src.json()) else {}

        # Cria linha local
        payload = {"k": STATE_KEY_LOCAL, "v": snapshot}
        url_insert = f"{SUPABASE_URL}/rest/v1/{TABLE}"
        r_insert = requests.post(url_insert, headers=headers, data=json.dumps(payload), timeout=10)
        if r_insert.status_code in (200, 201):
            st.sidebar.success("✅ Linha local criada automaticamente.")
        else:
            st.sidebar.warning(f"⚠️ Falha ao criar linha local: {r_insert.text}")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao garantir linha local: {e}")


def garantir_estado_nuvem_existe():
    """
    Garante que a linha principal dos robôs (curto_przo_v1) exista no Supabase.
    Se não existir, cria com a mesma estrutura padrão da linha local.
    """
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        # Verifica se já existe
        url_check = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_CLOUD}&select=k"
        r_check = requests.get(url_check, headers=headers, timeout=10)
        if r_check.status_code == 200 and r_check.json():
            return  # já existe

        # Estrutura padrão (igual à local)
        estrutura_padrao = {
            "ativos": [],
            "status": {},
            "pausado": False,
            "disparos": {},
            "em_contagem": {},
            "tempo_acumulado": {},
            "historico_alertas": [],
            "log_monitoramento": [],
            "precos_historicos": {},
            "ultimo_estado_pausa": None,
            "ultimo_update_tempo": {},
            "ultimo_ping_keepalive": None,
            "ultima_data_abertura_enviada": None,
        }

        payload = {"k": STATE_KEY_CLOUD, "v": estrutura_padrao}
        url_insert = f"{SUPABASE_URL}/rest/v1/{TABLE}"
        r_insert = requests.post(url_insert, headers=headers, data=json.dumps(payload), timeout=10)
        if r_insert.status_code in (200, 201):
            st.sidebar.success("✅ Linha da nuvem criada com estrutura padrão.")
        else:
            st.sidebar.warning(f"⚠️ Falha ao criar linha da nuvem: {r_insert.text}")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao garantir linha da nuvem: {e}")



# -----------------------------------------------------
#  snapshot, salvar, carregar e apagar
# -----------------------------------------------------
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

    # 1️⃣ Salva o estado local normalmente
    payload_local = {"k": STATE_KEY_LOCAL, "v": snapshot}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    try:
        r_local = requests.post(url, headers=headers, data=json.dumps(payload_local), timeout=15)
        if r_local.status_code not in (200, 201, 204):
            st.sidebar.error(f"Erro ao salvar estado local: {r_local.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado local: {e}")

    # 2️⃣ Envia cópia do mesmo estado para a linha da nuvem
    payload_cloud = {"k": STATE_KEY_CLOUD, "v": snapshot}
    try:
        r_cloud = requests.post(url, headers=headers, data=json.dumps(payload_cloud), timeout=15)
        if r_cloud.status_code not in (200, 201, 204):
            st.sidebar.warning(f"⚠️ Falha ao atualizar linha da nuvem: {r_cloud.text}")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao atualizar nuvem: {e}")

    # 3️⃣ Salva localmente (arquivo JSON)
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
                    precos_reconv = {
                        t: [(datetime.datetime.fromisoformat(dt) if isinstance(dt, str) else dt, p) for dt, p in dados]
                        for t, dados in v.items()
                    }
                    st.session_state[k] = precos_reconv
                elif k == "disparos":
                    disparos_reconv = {
                        t: [(datetime.datetime.fromisoformat(pt) if isinstance(pt, str) else pt, p) for pt, p in pontos]
                        for t, pontos in v.items()
                    }
                    st.session_state[k] = disparos_reconv
                else:
                    st.session_state[k] = v
            st.sidebar.info("Conectado na nuvem (linha local) ☁️")
            remoto_ok = True
            origem = "☁️ Supabase"
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto ainda (linha local).")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")

    if not remoto_ok and os.path.exists(LOCAL_STATE_FILE):
        try:
            with open(LOCAL_STATE_FILE, "r", encoding="utf-8") as f:
                estado = json.load(f)
            for k, v in estado.items():
                if k == "precos_historicos":
                    precos_reconv = {
                        t: [(datetime.datetime.fromisoformat(dt) if isinstance(dt, str) else dt, p) for dt, p in dados]
                        for t, dados in v.items()
                    }
                    st.session_state[k] = precos_reconv
                elif k == "disparos":
                    disparos_reconv = {
                        t: [(datetime.datetime.fromisoformat(pt) if isinstance(pt, str) else pt, p) for pt, p in pontos]
                        for t, pontos in v.items()
                    }
                    st.session_state[k] = disparos_reconv
                else:
                    st.session_state[k] = v
            st.sidebar.info("💾 Estado carregado do local (fallback)!")
            origem = "📁 Local"
        except Exception as e:
            st.sidebar.error(f"Erro no fallback local: {e}")

    for t in st.session_state.get("tempo_acumulado", {}):
        if st.session_state.tempo_acumulado.get(t, 0) > 0 and not st.session_state.ultimo_update_tempo.get(t):
            st.session_state.ultimo_update_tempo[t] = agora_lx().isoformat()

    st.session_state["origem_estado"] = origem
    st.session_state["__carregado_ok__"] = (origem in ("☁️ Supabase", "📁 Local"))


def apagar_estado_remoto(keys=None):
    """
    Apaga múltiplas linhas no Supabase (usado no botão 🧹).
    """
    keys = keys or [STATE_KEY]
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    for k in keys:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{k}"
        try:
            r = requests.delete(url, headers=headers, timeout=15)
            st.session_state.log_monitoramento.append(
                f"{agora_lx().strftime('%H:%M:%S')} | DEBUG: apagar_estado_remoto({k}) status = {r.status_code}"
            )
            if r.status_code == 204:
                st.sidebar.success(f"✅ Estado remoto apagado: {k}")
            else:
                st.sidebar.error(f"Erro ao apagar {k}: {r.status_code} - {r.text}")
        except Exception as e:
            st.sidebar.error(f"Erro ao apagar {k}: {e}")


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


# --- inicialização (ordem correta) ---
inicializar_estado()
garantir_estado_nuvem_existe()   # garante que a linha dos robôs exista
garantir_estado_local_existe()   # garante que a linha local exista
carregar_estado_duravel()
st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | Robô iniciado - Workflow GitHub ativo")


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
            # Se o corpo não for HTML, envia como texto simples (compatibilidade)
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
                    text=f"{texto_final}\n\n🤖 Robot 1milhão Invest",
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

def notificar_preco_alvo_alcancado_curto(ticker, preco_alvo, preco_atual, operacao):
    """
    Gera e envia alertas (Telegram + e-mail) com template visual e compliance.
    Compatível com enviar_notificacao_curto().
    """
    # --- Formata mensagens com visual e compliance ---
    msg_telegram, msg_email_html = formatar_mensagem_alerta(ticker, preco_alvo, preco_atual, operacao)

    # --- Ajusta campos e parâmetros ---
    tk_sem_ext = ticker.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else operacao.upper()
    assunto = f"ALERTA CURTO PRAZO: {msg_op} em {tk_sem_ext}"

    # --- Credenciais (st.secrets) ---
    remetente = st.secrets.get("email_sender", "")
    senha = st.secrets.get("gmail_app_password", "")
    destinatario = st.secrets.get("email_recipient_curto", "")
    token_tg = st.secrets.get("telegram_token", "")
    chat_id = st.secrets.get("telegram_chat_id_curto", "")

    # --- Envio centralizado (função já existente no seu código) ---
    try:
        enviar_notificacao_curto(destinatario, assunto, msg_email_html, remetente, senha, token_tg, chat_id, msg_telegram)
        st.session_state.log_monitoramento.append(f"📤 Alerta enviado: {tk_sem_ext} ({msg_op})")
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ Erro no envio de alerta: {e}")

    return f"💥 ALERTA de {msg_op} em {tk_sem_ext} enviado com sucesso!"


def formatar_mensagem_alerta(ticker_symbol, preco_alvo, preco_atual, operacao):
    """
    Gera o texto formatado de alerta para envio por Telegram e E-mail.
    Inclui mensagem principal + compliance em tamanho reduzido (visual).
    """
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"

    # --- Texto para Telegram (HTML) ---
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

    # --- Corpo HTML do e-mail (dark, título azul, compliance pequeno/cinza) ---
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
    """Envia notificação de pregão aberto no máximo uma vez por dia."""
    now = agora_lx()
    data_atual = now.date()
    ultima_data_envio = st.session_state.get("ultima_data_abertura_enviada")

    if ultima_data_envio == str(data_atual):
        return

    try:
        tok = st.secrets.get("telegram_token", "").strip()
        chat = st.secrets.get("telegram_chat_id_curto", "").strip()
        if tok and chat:
            bot = Bot(token=tok)
            asyncio.run(bot.send_message(chat_id=chat, text="🤖 Robô iniciando monitoramento — Pregão Aberto!"))
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | 📣 Telegram: Pregão Aberto")
        else:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Telegram não configurado.")
    except Exception as e:
        st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Erro Telegram: {e}")

    st.session_state["ultima_data_abertura_enviada"] = str(data_atual)
    salvar_estado_duravel(force=True)

# -----------------------------
# INTERFACE E SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("🧹 Limpar Tabela"):
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }

        # 1️⃣ Zera os dados das linhas (local e nuvem) em vez de apagar
        for k in REMOTE_KEYS:
            payload = {"v": {}}  # mantém a linha, mas limpa os dados
            url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{k}"
            r = requests.patch(url, headers=headers, data=json.dumps(payload), timeout=15)
            if r.status_code in (200, 204):
                st.sidebar.success(f"✅ Linha '{k}' zerada com sucesso.")
            else:
                st.sidebar.warning(f"⚠️ Falha ao zerar '{k}': {r.status_code} - {r.text}")

        # 2️⃣ Limpa arquivo local
        try:
            if os.path.exists(LOCAL_STATE_FILE):
                os.remove(LOCAL_STATE_FILE)
        except Exception as e_local:
            st.sidebar.warning(f"⚠️ Erro ao apagar arquivo local: {e_local}")

        # 3️⃣ Limpa session_state e re-inicializa
        st.session_state.clear()
        inicializar_estado()

        # 4️⃣ Atualiza registro da data e salva
        st.session_state["ultima_data_abertura_enviada"] = str(agora_lx().date())
        salvar_estado_duravel(force=True)

        st.sidebar.success("✅ Dados da nuvem e local zerados (linhas mantidas).")
        st.rerun()

    except Exception as e:
        st.sidebar.error(f"Erro ao limpar tabela: {e}")





if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")
# -----------------------------------------
# TESTE COMPLETO DE ALERTA (com layout e compliance)
# -----------------------------------------
if st.sidebar.button("📩 Testar mensagem"):
    st.sidebar.info("Gerando alerta simulado...")

    ticker_teste = "PETR4.SA"
    preco_alvo = 37.50
    preco_atual = 37.52
    operacao = "compra"

    try:
        msg = notificar_preco_alvo_alcancado_curto(ticker_teste, preco_alvo, preco_atual, operacao)
        st.sidebar.success("✅ Mensagem de teste enviada (verifique Telegram e e-mail).")
        st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | 🧪 Teste estilizado executado com sucesso.")
    except Exception as e:
        st.sidebar.error(f"❌ Erro no teste: {e}")
        st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | ⚠️ Erro teste estilizado: {e}")

st.sidebar.checkbox("⏸️ Pausar monitoramento", key="pausado")
salvar_estado_duravel()

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")

if st.sidebar.button("🧹 Limpar Histórico"):
    st.session_state.historico_alertas.clear()
    salvar_estado_duravel(force=True)
    st.sidebar.success("Histórico limpo!")
if st.sidebar.button("🧹 Limpar Monitoramento"):
    st.session_state.log_monitoramento.clear()
    salvar_estado_duravel(force=True)
    st.sidebar.success("Log limpo!")
    
if st.sidebar.button("🧹 Limpar Gráfico ⭐"):
    # Limpa estrelas de disparo
    st.session_state.disparos = {}

    # Mantém apenas os históricos dos tickers ainda ativos na tabela
    ativos_atuais = {a["ticker"] for a in st.session_state.ativos}
    st.session_state.precos_historicos = {
        t: dados for t, dados in st.session_state.precos_historicos.items() if t in ativos_atuais
    }

    # Também limpa status e acumuladores de quem já saiu
    st.session_state.tempo_acumulado = {
        t: v for t, v in st.session_state.tempo_acumulado.items() if t in ativos_atuais
    }
    st.session_state.em_contagem = {
        t: v for t, v in st.session_state.em_contagem.items() if t in ativos_atuais
    }
    st.session_state.status = {
        t: v for t, v in st.session_state.status.items() if t in ativos_atuais
    }

    # Salva e confirma visualmente
    salvar_estado_duravel(force=True)
    st.sidebar.success("Marcadores e históricos antigos limpos!")


tickers_existentes = sorted(set(a["ticker"] for a in st.session_state.ativos)) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

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

st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")
st.write("Robô automático da **CARTEIRA CURTO PRAZO** — dispara alerta após 25 min na zona de preço alvo.")

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

if st.button("➕ Adicionar ativo"):
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
                st.success(f"Ativo {ticker} adicionado e gráfico inicializado.")
            else:
                st.warning(f"Ativo {ticker} adicionado, sem preço inicial.")
        except Exception as e:
            st.error(f"Erro ao coletar preço de {ticker}: {e}")
        salvar_estado_duravel(force=True)
# -----------------------------
# STATUS + GRÁFICO + LOG
# -----------------------------
# STATUS + GRÁFICO + LOG
# -----------------------------
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()
grafico = st.empty()
st.subheader("🕒 Monitoramento")
log_container = st.empty()

# -----------------------------
# -----------------------------
# LOOP DE MONITORAMENTO (VERSÃO CORRIGIDA + DEBUG)
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
            operacao = ativo["operacao"].upper()
            tempo = st.session_state.tempo_acumulado.get(t, 0)
            minutos = tempo / 60
            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception:
                pass
    
            data.append({
                "Ticker": t,
                "Operação": operacao,
                "Preço Alvo": f"R$ {preco_alvo:.2f}",
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
                "Operação": ativo["operacao"].upper(),
                "Preço Alvo": f"R$ {ativo['preco']:.2f}",
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
            operacao_atv = ativo["operacao"]
            tk_full = f"{t}.SA"

            try:
                preco_atual = obter_preco_atual(tk_full)
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro {t}: {e}")
                continue

            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | {tk_full}: R$ {preco_atual:.2f}")

            condicao = (
                (operacao_atv == "compra" and preco_atual >= preco_alvo) or
                (operacao_atv == "venda" and preco_atual <= preco_alvo)
            )

            # -----------------------------
            # BLOCO PRINCIPAL DE CONTAGEM (CORRIGIDO + DEBUG)
            # -----------------------------
            if condicao:
                st.session_state.status[t] = "🟡 Em contagem"

                if not st.session_state.em_contagem.get(t, False):
                    # Inicia contagem
                    st.session_state.em_contagem[t] = True
                    if not st.session_state.ultimo_update_tempo.get(t) and st.session_state.tempo_acumulado.get(t, 0) == 0:
                        st.session_state.tempo_acumulado[t] = 0
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando/retomando contagem..."
                    )
                    salvar_estado_duravel(force=True)

                else:
                    # Continua contagem
                    ultimo = st.session_state.ultimo_update_tempo.get(t)
                    st.session_state.log_monitoramento.append(
                        f"🐞 DEBUG {t}: ultimo_update_tempo bruto = {ultimo}"
                    )

                    # 🕒 Conversão robusta
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

                    
                   
                    # 🧮 Calcula delta real entre o último update e agora
                    delta = (now - dt_ultimo).total_seconds()
                    
                    # Proteção mínima: se der delta negativo, zera (ex: relógio do servidor muda)
                    if delta < 0:
                        delta = 0
                    
                    # Atualiza acumulador e timestamp
                    st.session_state.tempo_acumulado[t] = float(st.session_state.tempo_acumulado.get(t, 0)) + float(delta)
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    
                    # Loga o incremento real
                    st.session_state.log_monitoramento.append(
                        f"⌛ {t}: {int(st.session_state.tempo_acumulado[t])}s acumulados (+{int(delta)}s)"
                    )
                    
                    # Salva imediatamente o estado
                    salvar_estado_duravel(force=True)

                # 🚀 Disparo de alerta quando atinge o tempo máximo
                if (
                    st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO
                    and st.session_state.status.get(t) != "🚀 Disparado"
                ):
                    st.session_state.status[t] = "🚀 Disparado"
                    alerta_msg = notificar_preco_alvo_alcancado_curto(tk_full, preco_alvo, preco_atual, operacao_atv)
                    st.warning(alerta_msg)
                    st.session_state.historico_alertas.append({
                        "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "ticker": t,
                        "operacao": operacao_atv,
                        "preco_alvo": preco_alvo,
                        "preco_atual": preco_atual
                    })
                    st.session_state.disparos.setdefault(t, []).append((now, preco_atual))
                    tickers_para_remover.append(t)
                    salvar_estado_duravel(force=True)

            else:
                # Saiu da zona de preço
                if st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🔴 Fora da zona"
                    st.session_state.ultimo_update_tempo[t] = None
                    st.session_state.log_monitoramento.append(f"❌ {t} saiu da zona de preço alvo.")
                    salvar_estado_duravel(force=True)

        if tickers_para_remover:
            st.session_state.ativos = [a for a in st.session_state.ativos if a["ticker"] not in tickers_para_remover]
            for t in tickers_para_remover:
                st.session_state.tempo_acumulado.pop(t, None)
                st.session_state.em_contagem.pop(t, None)
                st.session_state.status[t] = "✅ Ativado (removido)"
                st.session_state.ultimo_update_tempo.pop(t, None)
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | 🧹 Removidos após ativação: {', '.join(tickers_para_remover)}"
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
            APP_URL = "https://curtoprazo.streamlit.app"
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
                                 line=dict(color=st.session_state.ticker_colors.get(t, "#3b82f6"), width=2)))
for t, pontos in st.session_state.disparos.items():
    if pontos:
        xs, ys = zip(*pontos)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name=f"Ativação {t}",
                                 marker=dict(symbol="star", size=12, line=dict(width=2, color="white"))))
fig.update_layout(title="📉 Evolução dos Preços", template="plotly_dark")
grafico.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG E AUTOREFRESH
# -----------------------------
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]
    salvar_estado_duravel()
# ---------- LOG: cor por ticker + box rolável + ordem decrescente ----------

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
    """Renderiza o log no mesmo estilo visual do clube.py (cores, badges, rolagem)."""
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
            st.download_button("⬇️ Baixar state_curto.json",
                               data=json.dumps(state_preview, indent=2),
                               file_name="state_curto.json", mime="application/json")
        else:
            st.info("Nenhum estado salvo ainda.")
    except Exception as e:
        st.error(f"Erro ao exibir JSON: {e}")


refresh_ms = 300_000  # atualização visual a cada 50 segundos (não afeta lógica de tempo)
st_autorefresh(interval=refresh_ms, limit=None, key="curto-refresh")
