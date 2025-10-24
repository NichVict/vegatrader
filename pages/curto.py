# CURTO.PY - ENVIO DE ORDENS (INTERFACE FININHA: SOMENTE ENVIA DADOS / LIMPA / TESTES)
# -*- coding: utf-8 -*-

import streamlit as st
# from yahooquery import Ticker  # 🚫 DESATIVADO: interface não coleta preço localmente
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
# from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type  # 🚫 sem uso agora
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
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)

# Interface não fará monitoramento local; intervalos só afetam autorefresh visual
INTERVALO_VERIFICACAO = 300
LOG_MAX_LINHAS = 1000
PERSIST_DEBOUNCE_SECONDS = 60

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# =============================
# PERSISTÊNCIA (SUPABASE via REST API)
# =============================
SUPABASE_URL = st.secrets["supabase_url_curto"]
SUPABASE_KEY = st.secrets["supabase_key_curto"]
TABLE = "kv_state_curto"
STATE_KEY = "curto_przo_v1"

# ⚠️ Removido fallback local (arquivo). Mantido só para limpeza, se existir.
LOCAL_STATE_FILE = "session_data/state_curto.json"  # não mais utilizado para carregar/salvar


def agora_lx():
    return datetime.datetime.now(TZ)

def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def inicializar_estado():
    # Estado mínimo para UI; NÃO geramos/gravamos logs locais
    defaults = {
        "ativos": [],
        "historico_alertas": [],
        "log_monitoramento": [],  # será apenas o que vier da nuvem; interface não escreve
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
    ensure_color_map()


def carregar_estado_duravel():
    """Carrega o estado da nuvem e coloca em st.session_state SEM criar logs locais."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}?k=eq.{STATE_KEY}&select=v"
    origem = "❌ Nenhum"

    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and r.json():
            estado = r.json()[0]["v"]
            # Cópia literal do estado da nuvem (inclusive log_monitoramento feito pelo robô da nuvem)
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
    """Salva TODO o estado atual no Supabase.
       IMPORTANTE: a interface NÃO gera logs locais, logo não polui o que veio da nuvem."""
    snapshot = {
        # Mandamos exatamente os campos presentes no session_state (já carregados da nuvem),
        # com eventuais alterações que a interface fizer (ex.: adicionar/remover ativos).
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),  # só leitura na interface
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "disparos": st.session_state.get("disparos", {}),
        "ultima_data_abertura_enviada": st.session_state.get("ultima_data_abertura_enviada", None),
    }

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

    # 🚫 DESATIVADO: não salvamos fallback local
    # try:
    #     os.makedirs("session_data", exist_ok=True)
    #     with open(LOCAL_STATE_FILE, "w", encoding="utf-8") as f:
    #         json.dump(snapshot, f, ensure_ascii=False, indent=2)
    # except Exception as e:
    #     st.sidebar.warning(f"⚠️ Erro ao salvar local: {e}")

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
        r = requests.delete(url, headers=headers, timeout=15)
        if r.status_code == 204:
            st.sidebar.success("✅ Estado remoto apagado com sucesso!")
        else:
            st.sidebar.error(f"Erro ao apagar estado remoto: {r.status_code} - {r.text}")
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado remoto: {e}")

# -----------------------------
# FUNÇÕES DE NOTIFICAÇÃO (MANTIDAS PARA TESTE MANUAL)
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
    # --- E-mail (HTML ou texto simples) ---
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
        except Exception:
            pass  # teste manual, evitar falha quebrar UI

    # --- Telegram ---
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
        except Exception:
            pass
    asyncio.run(send_tg())

def formatar_mensagem_alerta(ticker_symbol, preco_alvo, preco_atual, operacao):
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

# -----------------------------
# BOOT: CARREGA ESTADO DA NUVEM
# -----------------------------
inicializar_estado()
carregar_estado_duravel()

# -----------------------------
# INTERFACE E SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

if st.sidebar.button("🧹 Limpar Tabela"):
    try:
        # 1) Apaga remoto (Supabase)
        apagar_estado_remoto()

        # 2) Apaga arquivo local, se existir
        try:
            if os.path.exists(LOCAL_STATE_FILE):
                os.remove(LOCAL_STATE_FILE)
        except Exception as e_local:
            st.sidebar.warning(f"⚠️ Erro ao apagar arquivo local: {e_local}")

        # 3) Limpa session_state COMPLETAMENTE
        for key in list(st.session_state.keys()):
            del st.session_state[key]

        st.sidebar.success("✅ Todos os dados e o estado local foram apagados com sucesso!")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram())
    st.sidebar.success("✅ Mensagem enviada!" if ok else f"❌ Falha: {erro}")

# TESTE COMPLETO DE ALERTA (mantido para checagem manual)
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

# Controles de limpeza de dados *locais* (não geramos log local, mas preservamos botões)
st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.get("historico_alertas"):
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda (aguardando dados da nuvem).")

if st.sidebar.button("🧹 Limpar Histórico"):
    # Limpa histórico local e remoto (mantendo restante do estado)
    st.session_state["historico_alertas"] = []
    salvar_estado_duravel(force=True)
    st.sidebar.success("Histórico limpo!")

if st.sidebar.button("🧹 Limpar Monitoramento"):
    # Interface NÃO escreve log local — este limpa o que veio da nuvem (se desejar).
    st.session_state["log_monitoramento"] = []
    salvar_estado_duravel(force=True)
    st.sidebar.success("Log limpo!")

if st.sidebar.button("🧹 Limpar Gráfico ⭐"):
    # Limpa marcadores e históricos locais (espelho do que veio da nuvem)
    st.session_state["disparos"] = {}
    ativos_atuais = {a["ticker"] for a in st.session_state.get("ativos", [])}
    st.session_state["precos_historicos"] = {
        t: dados for t, dados in st.session_state.get("precos_historicos", {}).items() if t in ativos_atuais
    }
    salvar_estado_duravel(force=True)
    st.sidebar.success("Marcadores e históricos antigos limpos!")

tickers_existentes = sorted(set(a["ticker"] for a in st.session_state.get("ativos", []))) if st.session_state.get("ativos") else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL (READ-ONLY + ENVIO DE DADOS)
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
st.write("Interface **operacional** da CARTEIRA CURTO PRAZO — leitura do estado da nuvem e envio de dados. "
         "O robô da nuvem é o único responsável por monitorar e disparar alertas.")

# ---- Entrada de dados: adiciona ativo (apenas escreve no estado e salva na nuvem) ----
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
        # Atualiza somente a lista de ativos; sem iniciar monitoramento/contagem local
        novo = {"ticker": ticker, "operacao": operacao, "preco": float(preco)}
        atuais = st.session_state.get("ativos", [])
        # evita duplicado exato
        if not any(a["ticker"] == ticker and a["operacao"] == operacao and float(a["preco"]) == float(preco) for a in atuais):
            atuais.append(novo)
            st.session_state["ativos"] = atuais
            salvar_estado_duravel(force=True)
            st.success(f"Ativo {ticker} enviado para a nuvem.")
        else:
            st.warning("Esse ativo já está na lista com os mesmos parâmetros.")

# -----------------------------
# STATUS (READ-ONLY), GRÁFICO e LOG (da nuvem)
# -----------------------------
st.subheader("📊 Status dos Ativos (Nuvem)")
tabela_status = st.empty()
grafico = st.empty()
st.subheader("🕒 Monitoramento (Robô da Nuvem)")
log_container = st.empty()

# Tabela: renderizamos o que vier do estado remoto (sem buscar preços locais)
if st.session_state.get("ativos"):
    data = []
    for ativo in st.session_state["ativos"]:
        t = ativo["ticker"]
        data.append({
            "Ticker": t,
            "Operação": ativo["operacao"].upper(),
            "Preço Alvo": f"R$ {float(ativo['preco']):.2f}",
            "Status": st.session_state.get("status", {}).get(t, "—"),
        })
    tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)
else:
    tabela_status.info("Nenhum ativo na lista. Adicione acima para enviar à nuvem.")

# GRÁFICO: somente com dados vindos da nuvem (se o robô nuvem preencher)
fig = go.Figure()
for t, dados in st.session_state.get("precos_historicos", {}).items():
    if len(dados) > 0:
        # dados no formato [(datetime_str_or_dt, preco)]
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
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=t,
                                 line=dict(color=st.session_state["ticker_colors"].get(t, "#3b82f6"), width=2)))
for t, pontos in st.session_state.get("disparos", {}).items():
    if pontos:
        xs, ys = [], []
        for dtv, pv in pontos:
            if isinstance(dtv, str):
                try:
                    xs.append(datetime.datetime.fromisoformat(dtv))
                except Exception:
                    xs.append(dtv)
            else:
                xs.append(dtv)
            ys.append(pv)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name=f"Ativação {t}",
                                 marker=dict(symbol="star", size=12, line=dict(width=2, color="white"))))
fig.update_layout(title="📉 Evolução dos Preços (Robô da Nuvem)", template="plotly_dark")
grafico.plotly_chart(fig, use_container_width=True)

# -----------------------------
# LOG (apenas o que veio da nuvem) + filtro
# -----------------------------
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

def color_for_ticker(ticker_):
    ensure_color_map()
    if ticker_ not in st.session_state["ticker_colors"]:
        idx = len(st.session_state["ticker_colors"]) % len(PALETTE)
        st.session_state["ticker_colors"][ticker_] = PALETTE[idx]
    return st.session_state["ticker_colors"][ticker_]

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
        st.write("— (sem entradas do robô da nuvem ainda) —")
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
    render_log_html(st.session_state.get("log_monitoramento", []), selected_tickers, 250)

# -----------------------------
# 🚫 DESATIVADO: LOOP DE MONITORAMENTO / ENVIO AUTOMÁTICO LOCAL
# (Toda lógica de contagem, preço, disparo e logs fica na nuvem)
# -----------------------------
"""
# EX-BLOCO (referência preservada):
# if dentro_pregao(now):
#     ... obter_preco_atual(...)
#     ... contagem TEMPO_ACUMULADO_MAXIMO ...
#     if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
#         # 🚫 Envio desativado - agora feito pela nuvem
#         # alerta_msg = notificar_preco_alvo_alcancado_curto(...)
#         # st.warning(alerta_msg)
#         ...
"""

# -----------------------------
# DEBUG / BACKUP (somente leitura da nuvem)
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

# -----------------------------
# AUTOREFRESH (reduzido para acompanhar a nuvem com mais frequência)
# -----------------------------
refresh_ms = 60_000  # 60s
st_autorefresh(interval=refresh_ms, limit=None, key="curto-refresh")
