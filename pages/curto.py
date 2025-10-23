# CURTO.PY 23/10 - SOMENTE USA O ROBOT DA NUVEM


# CURTO.PY - ENVIO DE ORDENS (versão nuvem: curto_przo_v1)
# -*- coding: utf-8 -*-

import streamlit as st
from yahooquery import Ticker
import datetime
import requests
import asyncio
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
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CURTO PRAZO - COMPRA E VENDA (Nuvem)", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(3, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(23, 59, 0)

INTERVALO_VERIFICACAO = 180
TEMPO_ACUMULADO_MAXIMO = 480
LOG_MAX_LINHAS = 1000
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# PERSISTÊNCIA (SUPABASE via REST API)
# =============================
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"

# Linha única da nuvem
STATE_KEY_CLOUD = "curto_przo_v1"
STATE_KEY = STATE_KEY_CLOUD  # esta instância usa somente a linha da nuvem

LOCAL_STATE_FILE = "session_data/state_curto.json"  # backup local opcional
PERSIST_DEBOUNCE_SECONDS = 60


def agora_lx():
    return datetime.datetime.now(TZ)


# -----------------------------------------------------
#  garantir que a linha da nuvem existe no Supabase
# -----------------------------------------------------
def garantir_estado_nuvem_existe():
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        url_check = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_CLOUD}&select=k"
        r_check = requests.get(url_check, headers=headers, timeout=10)
        if r_check.status_code == 200 and r_check.json():
            return  # já existe

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
        if r_insert.status_code not in (200, 201):
            st.sidebar.warning(f"⚠️ Falha ao criar linha da nuvem: {r_insert.text}")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao garantir linha da nuvem: {e}")


# -----------------------------------------------------
#  snapshot, salvar, carregar
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
    """Salva o estado atual apenas na linha da nuvem e backup local."""
    snapshot = _estado_snapshot()
    if not isinstance(snapshot, dict) or not snapshot:
        return

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    # Atualiza linha da nuvem (PATCH)
    try:
        url_patch = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_CLOUD}"
        r_cloud = requests.patch(url_patch, headers=headers, data=json.dumps({"v": snapshot}), timeout=15)
        if r_cloud.status_code not in (200, 204):
            st.sidebar.warning(f"⚠️ Falha ao atualizar nuvem: {r_cloud.status_code} - {r_cloud.text}")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao atualizar nuvem: {e}")

    # Backup local (opcional)
    try:
        os.makedirs("session_data", exist_ok=True)
        with open(LOCAL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Erro ao salvar arquivo local: {e}")

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
            st.session_state["origem_estado"] = "☁️ Supabase"
            remoto_ok = True
        else:
            st.sidebar.info("ℹ️ Nenhum estado remoto ainda (nuvem).")
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
            st.sidebar.info("💾 Estado carregado do local (fallback).")
            st.session_state["origem_estado"] = "📁 Local"

        except Exception as e:
            st.sidebar.error(f"Erro no fallback local: {e}")

    for t in st.session_state.get("tempo_acumulado", {}):
        if st.session_state.tempo_acumulado.get(t, 0) > 0 and not st.session_state.ultimo_update_tempo.get(t):
            st.session_state.ultimo_update_tempo[t] = agora_lx().isoformat()

    st.session_state["__carregado_ok__"] = True


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
garantir_estado_nuvem_existe()   # garante que a linha da nuvem exista
carregar_estado_duravel()
st.session_state.log_monitoramento.append(f"{agora_lx().strftime('%H:%M:%S')} | Robô (nuvem) iniciado - Workflow GitHub ativo")

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
# >>>>>>>>>>>>  ENVIO DE MENSAGENS DESATIVADO  <<<<<<<<<<<<<<
# Qualquer tentativa de enviar e-mail/telegram vira NO-OP + log.

def _log_only(msg: str):
    st.session_state.log_monitoramento.append(msg)
    salvar_estado_duravel()

def enviar_email(*args, **kwargs):
    _log_only(f"{agora_lx().strftime('%H:%M:%S')} | [NO-OP] Envio de e-mail desativado.")

def enviar_notificacao_curto(*args, **kwargs):
    _log_only(f"{agora_lx().strftime('%H:%M:%S')} | [NO-OP] Envio de Telegram/e-mail desativado.")

async def testar_telegram():
    return False, "Envio de mensagens desativado nesta versão (nuvem)."

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
    **Apenas loga** que o alerta teria sido disparado (sem envio real).
    """
    tk_sem_ext = ticker.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    st.session_state.log_monitoramento.append(
        f"{agora_lx().strftime('%H:%M:%S')} | 🚀 ALERTA (simulado) {msg_op} em {tk_sem_ext} | alvo {preco_alvo:.2f} | atual {preco_atual:.2f}"
    )
    salvar_estado_duravel(force=True)
    return f"💥🤖 ALERTA (simulado) de {msg_op} em {tk_sem_ext}"

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
    """Versão sem envio de mensagem: apenas registra no log uma vez por dia."""
    now = agora_lx()
    data_atual = now.date()
    ultima_data_envio = st.session_state.get("ultima_data_abertura_enviada")
    if ultima_data_envio == str(data_atual):
        return
    st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | 📣 Pregão Aberto (log, sem envio)")
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
        # Zera dados da linha da nuvem (mantém a linha)
        payload = {"v": {}}
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_CLOUD}"
        r = requests.patch(url, headers=headers, data=json.dumps(payload), timeout=15)
        if r.status_code in (200, 204):
            st.sidebar.success(f"✅ Linha '{STATE_KEY_CLOUD}' zerada com sucesso.")
        else:
            st.sidebar.warning(f"⚠️ Falha ao zerar '{STATE_KEY_CLOUD}': {r.status_code} - {r.text}")

        # Limpa arquivo local (backup)
        try:
            if os.path.exists(LOCAL_STATE_FILE):
                os.remove(LOCAL_STATE_FILE)
        except Exception as e_local:
            st.sidebar.warning(f"⚠️ Erro ao apagar arquivo local: {e_local}")

        # Limpa session_state e re-inicializa
        st.session_state.clear()
        inicializar_estado()

        # Atualiza registro da data e salva
        st.session_state["ultima_data_abertura_enviada"] = str(agora_lx().date())
        salvar_estado_duravel(force=True)

        st.sidebar.success("✅ Dados da nuvem zerados (linha mantida).")
        st.rerun()

    except Exception as e:
        st.sidebar.error(f"Erro ao limpar tabela: {e}")

# (Removidos botões de envio/ teste de mensagens)
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

    salvar_estado_duravel(force=True)
    st.sidebar.success("Marcadores e históricos antigos limpos!")


tickers_existentes = sorted(set(a["ticker"] for a in st.session_state.ativos)) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CURTO PRAZO - COMPRA E VENDA (Robô na Nuvem)")

origem = st.session_state.get("origem_estado", "❓")
st.markdown("🟢 **Origem dos dados:** Nuvem (Supabase)")
st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")
st.write("Robô automático da **CARTEIRA CURTO PRAZO** — dispara alerta (simulado) após 25 min na zona de preço alvo.")

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
st.subheader("🕒 Monitoramento (Log da Nuvem)")
log_container = st.empty()

# -----------------------------
# LOOP DE MONITORAMENTO
# -----------------------------
sleep_segundos = 60
if st.session_state.pausado:
    st.info("⏸️ Monitoramento pausado.")
else:
    now = agora_lx()
    # Exibe a tabela mesmo fora do pregão (mantém última atualização)
    if st.session_state.ativos:
        data = []
        now = agora_lx()
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_alvo = ativo["preco"]
            operacao_atv = ativo["operacao"].upper()
            tempo = st.session_state.tempo_acumulado.get(t, 0)
            minutos = tempo / 60
            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception:
                pass
    
            data.append({
                "Ticker": t,
                "Operação": operacao_atv,
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

            # BLOCO DE CONTAGEM
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
                    if ultimo:
                        try:
                            if isinstance(ultimo, str):
                                dt_ultimo = datetime.datetime.fromisoformat(ultimo)
                                if dt_ultimo.tzinfo is None:
                                    dt_ultimo = dt_ultimo.replace(tzinfo=TZ)
                            else:
                                dt_ultimo = ultimo
                        except Exception:
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

                # Disparo quando atinge o tempo máximo (simulado)
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
# LOG (APENAS NUVEM)
# -----------------------------
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]
    salvar_estado_duravel()

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
    if not lines:
        st.info("Nenhum log ainda.")
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

def carregar_log_nuvem():
    """Carrega o log_monitoramento da linha de nuvem (curto_przo_v1)."""
    try:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY_CLOUD}&select=v"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200 and r.json():
            dados = r.json()[0]["v"]
            log_nuvem = dados.get("log_monitoramento", [])
            return log_nuvem
        else:
            return []
    except Exception as e:
        st.warning(f"⚠️ Erro ao carregar log da nuvem: {e}")
        return []

# Exibe apenas o log da nuvem
st.subheader("☁️ Log do Robô (Nuvem)")
render_log_html(carregar_log_nuvem(), selected_tickers, 250)

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

# Atualização visual a cada 5 min (mantido)
refresh_ms = 300_000
st_autorefresh(interval=refresh_ms, limit=None, key="curto-refresh")
