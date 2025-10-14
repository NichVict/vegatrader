

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
st.set_page_config(page_title="CURTISSIMO PRAZO - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(10, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)

# produção
INTERVALO_VERIFICACAO = 60       # 5 min
TEMPO_ACUMULADO_MAXIMO = 180     # 25 min
LOG_MAX_LINHAS = 1000

# persistência (debounce)
PERSIST_DEBOUNCE_SECONDS = 60  # mantém baixo para Streamlit Cloud

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# =============================
# PERSISTÊNCIA (SUPABASE via REST API + LOCAL JSON)
# =============================
# Defina em st.secrets:
# supabase_url = "https://....supabase.co"
# supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
SUPABASE_URL = st.secrets["supabase_url"]
SUPABASE_KEY = st.secrets["supabase_key"]
TABLE = "kv_state_curtissimo"
STATE_KEY = "curtissimo_przo_v1"
LOCAL_STATE_FILE = "session_data/state_curtissimo.json"  # Para compatibilidade com Painel Central

def agora_lx():
    return datetime.datetime.now(TZ)

def _estado_snapshot():
    """
    Snapshot completo do estado — serializável (datetimes → ISO).
    """
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
        "avisou_abertura_pregao": st.session_state.get("avisou_abertura_pregao", False),

        "ultimo_ping_keepalive": st.session_state.get("ultimo_ping_keepalive", None),
    }

    # Serializa listas com datetimes
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
    """Grava o estado imediatamente (remoto + local) e atualiza o timestamp de última gravação."""
    snapshot = _estado_snapshot()

    # Supabase
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

    # Local
    try:
        os.makedirs("session_data", exist_ok=True)
        with open(LOCAL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao salvar local: {e}")

    st.session_state["__last_save_ts"] = agora_lx().timestamp()

def salvar_estado_duravel(force: bool = False):
    """
    Debounce de persistência para reduzir gravações e evitar sobrescrita fora de ordem.
    - force=True grava imediatamente (usar nas ações do usuário).
    - caso contrário, grava apenas se passaram PERSIST_DEBOUNCE_SECONDS desde a última gravação.
    """
    if force:
        _persist_now()
        return
    last = st.session_state.get("__last_save_ts")
    now_ts = agora_lx().timestamp()
    if not last or (now_ts - last) >= PERSIST_DEBOUNCE_SECONDS:
        _persist_now()

def carregar_estado_duravel():
    """
    Carrega do Supabase e, em fallback, do JSON local.
    Define __carregado_ok__=True em caso de sucesso.
    """
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    remoto_ok = False
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            pausado_atual = st.session_state.get("pausado")
            for k, v in estado.items():
                if k == "pausado" and pausado_atual is not None:
                    continue
                if k == "precos_historicos":
                    precos_reconv = {}
                    for t, dados in v.items():
                        reconv_dados = [(datetime.datetime.fromisoformat(dt_str) if isinstance(dt_str, str) else dt_str, p) for dt_str, p in dados]
                        precos_reconv[t] = reconv_dados
                    st.session_state[k] = precos_reconv
                elif k == "disparos":
                    disparos_reconv = {}
                    for t, pontos in v.items():
                        reconv_pontos = [(datetime.datetime.fromisoformat(pt_str) if isinstance(pt_str, str) else pt_str, p) for pt_str, p in pontos]
                        disparos_reconv[t] = reconv_pontos
                    st.session_state[k] = disparos_reconv
                else:
                    st.session_state[k] = v
            st.sidebar.info("💾 Estado (CURTISSIMO PRAZO) restaurado da nuvem!")
            remoto_ok = True
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto ainda.")
    except Exception as e:
        st.sidebar.error(f"Erro ao carregar estado remoto: {e}")

    origem = "❌ Nenhum"
    if remoto_ok:
        origem = "☁️ Supabase"
    else:
        if os.path.exists(LOCAL_STATE_FILE):
            try:
                with open(LOCAL_STATE_FILE, "r", encoding="utf-8") as f:
                    estado = json.load(f)
                pausado_atual = st.session_state.get("pausado")
                for k, v in estado.items():
                    if k == "pausado" and pausado_atual is not None:
                        continue
                    if k == "precos_historicos":
                        precos_reconv = {}
                        for t, dados in v.items():
                            reconv_dados = [(datetime.datetime.fromisoformat(dt_str) if isinstance(dt_str, str) else dt_str, p) for dt_str, p in dados]
                            precos_reconv[t] = reconv_dados
                        st.session_state[k] = precos_reconv
                    elif k == "disparos":
                        disparos_reconv = {}
                        for t, pontos in v.items():
                            reconv_pontos = [(datetime.datetime.fromisoformat(pt_str) if isinstance(pt_str, str) else pt_str, p) for pt_str, p in pontos]
                            disparos_reconv[t] = reconv_pontos
                        st.session_state[k] = disparos_reconv
                    else:
                        st.session_state[k] = v
                st.sidebar.info("💾 Estado carregado do local (fallback)!")
                origem = "📁 Local"
            except Exception as e:
                st.sidebar.error(f"Erro no fallback local: {e}")

    # 🔧 CONSISTÊNCIA pós-carregamento
    for t in st.session_state.get("tempo_acumulado", {}):
        if st.session_state.tempo_acumulado.get(t, 0) > 0 and not st.session_state.ultimo_update_tempo.get(t):
            st.session_state.ultimo_update_tempo[t] = agora_lx().isoformat()

    st.session_state["origem_estado"] = origem
    st.session_state["__carregado_ok__"] = (origem in ("☁️ Supabase", "📁 Local"))

def apagar_estado_remoto():
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
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

# -----------------------------
# INICIALIZAÇÃO SEGURA DO ESTADO
# -----------------------------
def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def inicializar_estado():
    """Inicializa as chaves do session_state somente se ainda não existirem."""
    defaults = {
        "ativos": [],
        "historico_alertas": [],
        "log_monitoramento": [],
        "tempo_acumulado": {},
        "em_contagem": {},
        "status": {},
        "precos_historicos": {},
        "ultimo_update_tempo": {},
        "pausado": False,
        "ultimo_estado_pausa": None,
        "disparos": {},
        "__last_save_ts": None,
        "__carregado_ok__": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    ensure_color_map()

# 1) Inicializa estrutura mínima
inicializar_estado()
# 2) Carrega estado persistente (sobrescreve o necessário)
carregar_estado_duravel()
# 3) Garante que o restante exista (se o carregamento não trouxe tudo)
inicializar_estado()
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

def enviar_notificacao_curto(dest, assunto, corpo, rem, senha, tok_tg, chat_id):
    # Email
    if senha and dest:
        try:
            enviar_email(dest, assunto, corpo, rem, senha)
        except Exception as e:
            st.session_state.log_monitoramento.append(f"⚠️ Erro e-mail: {e}")
    else:
        st.session_state.log_monitoramento.append("⚠️ Email não configurado.")
    # Telegram
    async def send_tg():
        try:
            if tok_tg and chat_id:
                bot = Bot(token=tok_tg)
                await bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest (CURTISSIMO PRAZO).")
        except Exception as e:
            st.session_state.log_monitoramento.append(f"⚠️ Erro Telegram: {e}")
    asyncio.run(send_tg())

@st.cache_data(ttl=60)
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
    tk_sem_ext = ticker.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else operacao.upper()
    msg = (f"Operação de {msg_op} em {tk_sem_ext} ativada!\n"
           f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
           "COMPLIANCE: decisão de compra/venda é do destinatário.")
    rem = st.secrets.get("email_sender", "")
    senha = st.secrets.get("gmail_app_password", "")
    dest = st.secrets.get("email_recipient_curtissimo", "")
    tok_tg = st.secrets.get("telegram_token", "")
    chat_id = st.secrets.get("telegram_chat_id_curtissimo", "")
    enviar_notificacao_curto(dest, f"ALERTA CURTISSIMO PRAZO: {msg_op} em {tk_sem_ext}",
                             msg, rem, senha, tok_tg, chat_id)
    return msg

async def testar_telegram():
    tok = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_curtissimo", "")
    try:
        if tok and chat:
            bot = Bot(token=tok)
            await bot.send_message(chat_id=chat, text="✅ Teste de alerta CURTISSIMO PRAZO funcionando!")
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

# -----------------------------
# INTERFACE E SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("🧹 Apagar estado salvo (reset total)"):
    try:
        apagar_estado_remoto()
        st.session_state.clear()
        inicializar_estado()
        st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | 🧹 Reset manual")
        salvar_estado_duravel(force=True)
        st.sidebar.success("✅ Estado apagado e reiniciado.")
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")

st.sidebar.checkbox("⏸️ Pausar monitoramento", key="pausado")
salvar_estado_duravel()  # debounce OK

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")
col1, col2 = st.sidebar.columns(2)
if col1.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    salvar_estado_duravel(force=True)
    st.sidebar.success("Histórico limpo!")
if col2.button("🧽 Limpar LOG"):
    st.session_state.log_monitoramento.clear()
    salvar_estado_duravel(force=True)
    st.sidebar.success("Log limpo!")
if st.sidebar.button("🧼 Limpar marcadores ⭐"):
    st.session_state.disparos = {}
    salvar_estado_duravel(force=True)
    st.sidebar.success("Marcadores limpos!")

tickers_existentes = sorted(set(a["ticker"] for a in st.session_state.ativos)) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CURTISSIMO PRAZO - COMPRA E VENDA")

origem = st.session_state.get("origem_estado", "❓")
st.markdown({
    "☁️ Supabase": "🟢 **Origem dos dados:** Nuvem (Supabase)",
    "📁 Local": "🟠 **Origem dos dados:** Local",
}.get(origem, "⚪ **Origem dos dados:** Desconhecida"))

st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")
st.write("Robô automático da **CARTEIRA CURTISSIMO PRAZO** — dispara alerta após 25 min na zona de preço alvo.")

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
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()
grafico = st.empty()
st.subheader("🕒 Log de Monitoramento")
log_container = st.empty()

# -----------------------------
# LOOP DE MONITORAMENTO
# -----------------------------
sleep_segundos = 60
if st.session_state.pausado:
    st.info("⏸️ Monitoramento pausado.")
else:
    now = agora_lx()
    if dentro_pregao(now):
        # Notificação na abertura do pregão
        if not st.session_state.get("avisou_abertura_pregao", False):
            st.session_state["avisou_abertura_pregao"] = True
            try:
                tok = st.secrets.get("telegram_token", "").strip()
                chat = st.secrets.get("telegram_chat_id_curtissimo", "").strip()
                if tok and chat:
                    bot = Bot(token=tok)
                    asyncio.run(bot.send_message(chat_id=chat, text="📈 Robô CURTISSIMO PRAZO ativo — Pregão Aberto!"))
                    st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | 📣 Telegram: Pregão Aberto")
                else:
                    st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Telegram não configurado.")
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⚠️ Erro Telegram: {e}")
            salvar_estado_duravel(force=True)

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
                "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
                "Status": st.session_state.status.get(t, "🟢 Monitorando"),
                "Tempo Acumulado": f"{int(minutos)} min"
            })
        if data:
            tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)

        # Lógica de monitoramento
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
                (operacao_atv == "venda"  and preco_atual <= preco_alvo)
            )

            if condicao:
                st.session_state.status[t] = "🟡 Em contagem"
                if not st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = True
                    # ✅ só zera tempo se nunca iniciou
                    if not st.session_state.ultimo_update_tempo.get(t) and st.session_state.tempo_acumulado.get(t, 0) == 0:
                        st.session_state.tempo_acumulado[t] = 0
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando/retomando contagem..."
                    )
                    salvar_estado_duravel(force=True)
                else:
                    ultimo = st.session_state.ultimo_update_tempo.get(t)
                    dt_ultimo = datetime.datetime.fromisoformat(ultimo) if ultimo else now
                    delta = max(0, min((now - dt_ultimo).total_seconds(), INTERVALO_VERIFICACAO + 5))
                    st.session_state.tempo_acumulado[t] = st.session_state.tempo_acumulado.get(t, 0) + delta
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⏱ {t}: {int(st.session_state.tempo_acumulado[t])}s acumulados (+{int(delta)}s)"
                    )
                    salvar_estado_duravel()

                if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
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
        # Fora do pregão — mantém sessão viva
        st.session_state["avisou_abertura_pregao"] = False
        faltam, prox_abertura = segundos_ate_abertura(now)
        components.html(f"""
        <div style="background:#0b1220;border:1px solid #1f2937;border-radius:10px;padding:12px;">
        ⏸️ Pregão fechado. Reabre em <b>{datetime.timedelta(seconds=faltam)}</b> (às {prox_abertura.strftime('%H:%M')}).
        </div>""", height=70)
        try:
            APP_URL = "https://curtoprazo.streamlit.app"
            ultimo_ping = st.session_state.get("ultimo_ping_keepalive")
            if isinstance(ultimo_ping, str):
                ultimo_ping = datetime.datetime.fromisoformat(ultimo_ping)
            if not ultimo_ping or (now - ultimo_ping).total_seconds() > 900:
                requests.get(APP_URL, timeout=5)
                st.session_state["ultimo_ping_keepalive"] = now.isoformat()
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | 🔄 Keep-alive enviado")
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

# Limita crescimento do log
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]
    salvar_estado_duravel()

# Exibe log
def extract_ticker(line):
    m = re.search(r"\b([A-Z0-9]{4,6})\b", line)
    return m.group(1) if m else None

def render_log_html(lines, selected_tickers=None, max_lines=250):
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]
    if selected_tickers:
        subset = [l for l in subset if extract_ticker(l) in selected_tickers]
    html = ["<div style='background:#0b1220;border-radius:8px;padding:10px;max-height:360px;overflow-y:auto;'>"]
    for l in subset:
        html.append(f"<div style='font-family:monospace;color:#e5e7eb;margin:2px 0;'>{l}</div>")
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

refresh_ms = 1000 * (INTERVALO_VERIFICACAO if dentro_pregao(agora_lx()) else sleep_segundos)
st_autorefresh(interval=refresh_ms, limit=None, key="curtissimo-refresh")
