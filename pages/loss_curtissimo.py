# -*- coding: utf-8 -*-
"""
loss_curtissimo.py
LOSS CURTÍSSIMO PRAZO (Streamlit) — Encerramento por STOP

- Dispara ENCERRAMENTO após 900s (15 min) na zona do preço-alvo
- Mensagens: "CARTEIRA CURTÍSSIMO PRAZO" (encerramento/stop)
- Credenciais: lidas de st.secrets
- Keep-alive: https://losscurtissimo.streamlit.app
"""
import streamlit as st
from yahooquery import Ticker
import datetime
import time
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

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CURTÍSSIMO - STOP !!!", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)   # Lisboa
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)   # Lisboa

INTERVALO_VERIFICACAO   = 300   # 5 min
TEMPO_ACUMULADO_MAXIMO  = 900   # 15 min
LOG_MAX_LINHAS          = 1000

PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# -----------------------------
# PERSISTÊNCIA DO ESTADO
# -----------------------------
SAVE_DIR = "session_data"
os.makedirs(SAVE_DIR, exist_ok=True)
SAVE_PATH = os.path.join(SAVE_DIR, "state_loss_curtissimo.json")

def salvar_estado():
    estado = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "disparos": st.session_state.get("disparos", {}),
        "tempo_acumulado": st.session_state.get("tempo_acumulado", {}),
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "pausado": st.session_state.get("pausado", False),
        "ultimo_estado_pausa": st.session_state.get("ultimo_estado_pausa", None),
        "ultimo_ping_keepalive": st.session_state.get("ultimo_ping_keepalive", None),
        "avisou_abertura_pregao": st.session_state.get("avisou_abertura_pregao", False),
        "ultimo_update_tempo": st.session_state.get("ultimo_update_tempo", {}),
    }
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, default=str, indent=2)
        st.session_state.log_monitoramento.append(
            f"{datetime.datetime.now(TZ).strftime('%H:%M:%S')} | 💾 Estado salvo ({os.path.basename(SAVE_PATH)})"
        )
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado: {e}")
        st.session_state.log_monitoramento.append(
            f"{datetime.datetime.now(TZ).strftime('%H:%M:%S')} | ⚠️ Erro ao salvar estado: {e}"
        )

def carregar_estado():
    if os.path.exists(SAVE_PATH):
        try:
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                estado = json.load(f)
            pausado_atual = st.session_state.get("pausado")
            for k, v in estado.items():
                if k == "pausado" and pausado_atual is not None:
                    continue
                st.session_state[k] = v
            if not st.session_state.get("estado_restaurado", False):
                st.session_state.estado_restaurado = True
                st.sidebar.info("💾 Estado (LOSS CURTÍSSIMO) restaurado com sucesso!")
                st.session_state.log_monitoramento.append(
                    f"{datetime.datetime.now(TZ).strftime('%H:%M:%S')} | 💾 Estado (LOSS CURTÍSSIMO) restaurado!"
                )
        except Exception as e:
            st.sidebar.error(f"Erro ao carregar estado: {e}")

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    mensagem = MIMEMultipart()
    mensagem["From"] = remetente
    mensagem["To"] = destinatario
    mensagem["Subject"] = assunto
    mensagem.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.starttls()
        servidor.login(remetente, senha_ou_token)
        servidor.send_message(mensagem)

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
    preco_atual = tk.history(period="3d")["close"].iloc[-1]
    return float(preco_atual)

def agora_lx():
    return datetime.datetime.now(TZ)

def dentro_pregao(dt_now):
    t = dt_now.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_abertura(dt_now):
    hoje_abre = dt_now.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=0, second=0, microsecond=0)
    hoje_fecha = dt_now.replace(hour=HORARIO_FIM_PREGAO.hour, minute=0, second=0, microsecond=0)
    if dt_now < hoje_abre:
        return int((hoje_abre - dt_now).total_seconds()), hoje_abre
    elif dt_now > hoje_fecha:
        amanha_abre = hoje_abre + datetime.timedelta(days=1)
        return int((amanha_abre - dt_now).total_seconds()), amanha_abre
    else:
        return 0, hoje_abre

# ---- Cores por ticker ----
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

def render_log_html(lines, selected_tickers=None, max_lines=200):
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]
    if selected_tickers:
        subset = [l for l in subset if (extract_ticker(l) in selected_tickers)]

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
      .log-line{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px; line-height: 1.35; margin: 2px 0; color: #e5e7eb;
        display: flex; align-items: baseline; gap: 8px;
      }
      .ts{ color:#9ca3af; min-width:64px; text-align:right; }
      .badge{ display:inline-block; padding:1px 8px; font-size:12px; border-radius:9999px; color:white; }
      .msg{ white-space: pre-wrap; }
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

# -----------------------------
# ESTADOS INICIAIS
# -----------------------------
# inicializa antes de carregar estado
if "log_monitoramento" not in st.session_state:
    st.session_state.log_monitoramento = []

for var in ["ativos", "historico_alertas", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"] else []

if "pausado" not in st.session_state:
    st.session_state.pausado = False
if "ultimo_estado_pausa" not in st.session_state:
    st.session_state.ultimo_estado_pausa = None
if "disparos" not in st.session_state:
    st.session_state.disparos = {}
ensure_color_map()

# Carrega estado persistido
carregar_estado()

# -----------------------------
# SIDEBAR
# -----------------------------
st.sidebar.header("⚙️ Configurações")

# Reset total
if st.sidebar.button("🧹 Apagar estado salvo (reset total)"):
    try:
        if os.path.exists(SAVE_PATH):
            os.remove(SAVE_PATH)
        st.session_state.clear()
        st.session_state.pausado = True
        st.session_state.ultimo_estado_pausa = None
        st.session_state.ativos = []
        st.session_state.historico_alertas = []
        st.session_state.log_monitoramento = []
        st.session_state.tempo_acumulado = {}
        st.session_state.em_contagem = {}
        st.session_state.status = {}
        st.session_state.precos_historicos = {}
        st.session_state.disparos = {}
        st.session_state.ultimo_update_tempo = {}
        now_tmp = agora_lx()
        st.session_state.log_monitoramento.append(f"{now_tmp.strftime('%H:%M:%S')} | 🧹 Reset manual (LOSS CURTÍSSIMO)")
        salvar_estado()
        st.sidebar.success("✅ Estado (LOSS CURTÍSSIMO) apagado e reiniciado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

# Botão de teste do Telegram
async def testar_telegram():
    token = st.secrets.get("telegram_token", "")
    chat = st.secrets.get("telegram_chat_id_losscurtissimo", "")
    try:
        if not token or not chat:
            raise ValueError("Defina telegram_token e telegram_chat_id_losscurtissimo em st.secrets.")
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat, text="✅ Teste de alerta LOSS CURTÍSSIMO funcionando!")
        return True, None
    except Exception as e:
        return False, str(e)

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste (usando st.secrets)...")
    ok, erro = asyncio.run(testar_telegram())
    if ok:
        st.sidebar.success("✅ Mensagem enviada com sucesso!")
    else:
        st.sidebar.error(f"❌ Falha: {erro}")

st.sidebar.checkbox("⏸️ Pausar monitoramento (modo edição)", key="pausado")

# Histórico
st.sidebar.header("📜 Histórico de Encerramentos")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** — {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | STOP: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum encerramento ainda.")

col_limp, col_limp2 = st.sidebar.columns(2)
if col_limp.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")
if col_limp2.button("🧽 Limpar LOG"):
    st.session_state.log_monitoramento.clear()
    st.sidebar.success("Log limpo!")
if st.sidebar.button("🧼 Limpar marcadores ⭐"):
    st.session_state.disparos = {}
    st.sidebar.success("Marcadores limpos!")

tickers_existentes = sorted(set([a["ticker"] for a in st.session_state.ativos])) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("🛑 CURTÍSSIMO - STOP !!!")
st.caption(
    f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
    f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}"
)
st.write("Cadastre tickers e STOP. O robô envia **ENCERRAMENTO** quando o preço permanece na zona por **15 minutos (900s)**.")

col1, col2, col3 = st.columns(3)
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação para zerar posição", ["compra", "venda"])
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
        st.session_state.precos_historicos.setdefault(ticker, [])
        st.session_state.ultimo_update_tempo[ticker] = None
        st.success(f"STOP de {ticker} adicionado com sucesso!")
        salvar_estado()

# -----------------------------
# STATUS + GRÁFICO + LOG
# -----------------------------
st.subheader("📊 Status dos STOPs Monitorados")
tabela_status = st.empty()

if st.session_state.ativos:
    data = []
    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        preco_atual = "-"
        try:
            preco_atual = obter_preco_atual(f"{t}.SA")
        except Exception:
            pass
        tempo = st.session_state.tempo_acumulado.get(t, 0)
        minutos = tempo / 60
        data.append({
            "Ticker": t,
            "Zerar com": ativo["operacao"].upper(),
            "STOP": f"R$ {ativo['preco']:.2f}",
            "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
            "Status": st.session_state.status.get(t, "🟢 Monitorando"),
            "Tempo Acumulado": f"{int(minutos)} min"
        })
    df = pd.DataFrame(data)
    tabela_status.dataframe(df, use_container_width=True, height=220)
else:
    st.info("Nenhum STOP cadastrado ainda.")

st.subheader("📉 Gráfico em Tempo Real dos Preços")
grafico = st.empty()

st.subheader("🕒 Log de Monitoramento")
countdown_container = st.empty()
log_container = st.empty()

# -----------------------------
# MENSAGENS (LOSS CURTÍSSIMO - STOP)
# -----------------------------
def montar_mensagem_encerramento_curtissimo(ticker_symbol_full, preco_alvo, preco_atual, operacao):
    ticker_symbol = ticker_symbol_full.replace(".SA", "")
    mensagem_operacao_anterior = "COMPRA" if operacao == "venda" else "VENDA A DESCOBERTO"
    mensagem = (
        f"Encerramento da operação de {mensagem_operacao_anterior} em {ticker_symbol}!\n"
        f"Realize a operação de {operacao.upper()} para zerar sua posição.\n"
        f"STOP {preco_alvo:.2f} foi atingido ou ultrapassado.\n\n"
        "COMPLIANCE: Esta mensagem é uma sugestão de compra/venda baseada em nossa CARTEIRA CURTÍSSIMO PRAZO. "
        "A compra ou venda é de total decisão e responsabilidade do Destinatário. Este e-mail contém informação "
        "CONFIDENCIAL de propriedade do Canal 1milhao e de seu DESTINATÁRIO tão somente. Se você NÃO for "
        "DESTINATÁRIO ou pessoa autorizada a recebê-lo, NÃO PODE usar, copiar, transmitir, retransmitir ou "
        "divulgar seu conteúdo (no todo ou em partes), estando sujeito às penalidades da LEI. A Lista de Ações "
        "do Canal 1milhao é devidamente REGISTRADA."
    )
    assunto = f"*ALERTA CARTEIRA CURTÍSSIMO PRAZO* Encerramento da Operação de {mensagem_operacao_anterior} em {ticker_symbol}"
    return assunto, mensagem

def notificar_preco_alvo_alcancado_STOP_CURTISSIMO(ticker_symbol, preco_alvo, preco_atual, operacao):
    remetente = st.secrets.get("email_sender", "avisoscanal1milhao@gmail.com")
    senha_ou_token = st.secrets.get("gmail_app_password", "")
    destinatario = st.secrets.get("email_recipient_losscurtissimo", "loss_curtissimo@googlegroups.com")
    token_telegram = st.secrets.get("telegram_token", "")
    chat_id = st.secrets.get("telegram_chat_id_losscurtissimo", "")

    assunto, mensagem = montar_mensagem_encerramento_curtissimo(ticker_symbol, preco_alvo, preco_atual, operacao)

    # E-mail
    try:
        if not senha_ou_token:
            raise ValueError("Defina gmail_app_password em st.secrets.")
        enviar_email(destinatario, assunto, mensagem, remetente, senha_ou_token)
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ Erro ao enviar e-mail: {e}")

    # Telegram
    try:
        if token_telegram and chat_id:
            bot = Bot(token=token_telegram)
            bot.send_message(chat_id=chat_id, text=f"{mensagem}\n\nRobot 1milhão Invest.")
        else:
            st.session_state.log_monitoramento.append("ℹ️ Aviso: token/chat_id não configurado — Telegram ignorado.")
    except Exception as e:
        st.session_state.log_monitoramento.append(f"⚠️ Erro ao enviar Telegram: {e}")

    return mensagem

# -----------------------------
# CICLO ÚNICO + REEXECUÇÃO
# -----------------------------
sleep_segundos = 60

if st.session_state.pausado != st.session_state.ultimo_estado_pausa:
    st.session_state.ultimo_estado_pausa = st.session_state.pausado

if st.session_state.pausado:
    pass
else:
    if dentro_pregao(now):
        # ---- Notificação única na abertura do pregão ----
        if not st.session_state.get("avisou_abertura_pregao", False):
            st.session_state["avisou_abertura_pregao"] = True
            try:
                token = st.secrets.get("telegram_token", "").strip()
                chat = st.secrets.get("telegram_chat_id_losscurtissimo", "").strip()
                if token and chat:
                    bot = Bot(token=token)
                    asyncio.run(bot.send_message(chat_id=chat, text="🛑 Robô LOSS CURTÍSSIMO ativo — Pregão Aberto! ⏱️"))
                    st.session_state.log_monitoramento.append(
                        f"{now.strftime('%H:%M:%S')} | 📣 Telegram: Pregão Aberto (LOSS CURTÍSSIMO)"
                    )
                else:
                    st.session_state.log_monitoramento.append(
                        f"{now.strftime('%H:%M:%S')} | ℹ️ token/chat_id não configurado — aviso de abertura ignorado."
                    )
            except Exception as e:
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | ⚠️ Erro ao avisar abertura: {e}"
                )

        # Remove countdown
        countdown_container.empty()

        # Atualiza tabela e monitora
        data = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            st.session_state.em_contagem.setdefault(t, False)
            st.session_state.status.setdefault(t, "🟢 Monitorando")
            st.session_state.ultimo_update_tempo.setdefault(t, None)

            tk_full = f"{t}.SA"
            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(tk_full)
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")

            if preco_atual != "-":
                st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))

            tempo = st.session_state.tempo_acumulado.get(t, 0)
            minutos = tempo / 60
            data.append({
                "Ticker": t,
                "Zerar com": ativo["operacao"].upper(),
                "STOP": f"R$ {ativo['preco']:.2f}",
                "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
                "Status": st.session_state.status.get(t, "🟢 Monitorando"),
                "Tempo Acumulado": f"{int(minutos)} min"
            })
        if data:
            tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)

        # ---- Lógica por ativo (15 min) ----
        tickers_para_remover = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_alvo = ativo["preco"]
            operacao_atv = ativo["operacao"]
            tk_full = f"{t}.SA"

            try:
                preco_atual = obter_preco_atual(tk_full)
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")
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
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⚠️ {t} atingiu o STOP ({preco_alvo:.2f}). Iniciando contagem..."
                    )
                else:
                    ultimo = st.session_state.ultimo_update_tempo.get(t)
                    dt_ultimo = None
                    if ultimo:
                        try:
                            dt_ultimo = datetime.datetime.fromisoformat(ultimo)
                        except Exception:
                            dt_ultimo = None
                    if dt_ultimo is None:
                        dt_ultimo = now
                    delta = (now - dt_ultimo).total_seconds()
                    if delta < 0:
                        delta = 0
                    st.session_state.tempo_acumulado[t] = st.session_state.tempo_acumulado.get(t, 0) + delta
                    st.session_state.ultimo_update_tempo[t] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"⏱ {t}: {int(st.session_state.tempo_acumulado[t])}s acumulados (+{int(delta)}s)"
                    )

                if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                    alerta_msg = notificar_preco_alvo_alcancado_STOP_CURTISSIMO(tk_full, preco_alvo, preco_atual, operacao_atv)
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

            else:
                if st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🔴 Fora do STOP"
                    st.session_state.ultimo_update_tempo[t] = None
                    st.session_state.log_monitoramento.append(
                        f"❌ {t} saiu da zona de STOP. Contagem reiniciada."
                    )

        # Remove ativos disparados
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

        # ---- Gráfico ----
        # ---- Gráfico (mostra mesmo com 1 ponto) ----
        fig = go.Figure()
        
        for t, dados in st.session_state.precos_historicos.items():
            if len(dados) >= 1:
                xs, ys = zip(*dados)
                nome_trace = t if len(dados) > 1 else f"{t} ⚠️ (1 ponto)"
                fig.add_trace(go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines+markers" if len(dados) > 1 else "markers",
                    name=nome_trace,
                    line=dict(color=color_for_ticker(t), width=2),
                    marker=dict(size=10 if len(dados) == 1 else 6)
                ))
            else:
                st.session_state.log_monitoramento.append(f"{t}: sem dados suficientes para plotar.")
        
        # Marcadores de encerramento ⭐
        for t, pontos in st.session_state.disparos.items():
            if not pontos:
                continue
            xs, ys = zip(*pontos)
            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="markers",
                name=f"Encerramento {t}",
                marker=dict(
                    symbol="star",
                    size=12,
                    color=color_for_ticker(t),
                    line=dict(width=2, color="white")
                ),
                hovertemplate=(f"{t}<br>%{{x|%Y-%m-%d %H:%M:%S}}"
                               "<br><b>ENCERRAMENTO</b>"
                               "<br>Preço: R$ %{y:.2f}<extra></extra>")
            ))
        
        fig.update_layout(
            title="📉 Evolução dos Preços (encerramentos ⭐)",
            xaxis_title="Tempo",
            yaxis_title="Preço (R$)",
            legend_title="Legenda",
            template="plotly_dark",
            height=500
        )
        
        grafico.plotly_chart(fig, use_container_width=True)


        sleep_segundos = INTERVALO_VERIFICACAO

    else:
        # ---- Fora do pregão ----
        st.session_state["avisou_abertura_pregao"] = False
        faltam, prox_abertura = segundos_ate_abertura(now)
        elem_id = f"cd-{uuid.uuid4().hex[:8]}"
        components.html(
            f"""
<div style="background:#0b1220;border:1px solid #1f2937;border-radius:10px;padding:12px 14px;">
  <span style="color:#9ca3af;">⏸️ Pregão fechado.</span>
  <span style="margin-left:8px; color:#e5e7eb;">
  Reabre em <b id="{elem_id}" style="color:#ffffff;">--:--:--</b>
  (às {prox_abertura.strftime('%H:%M')}). 
</span>
</div>
<script>
(function(){{
  var total={faltam};
  function fmt(s){{
    var h=Math.floor(s/3600), m=Math.floor((s%3600)/60), ss=s%60;
    return String(h).padStart(2,'0')+":"+String(m).padStart(2,'0')+":"+String(ss).padStart(2,'0');
  }}
  function tick(){{
    var el=document.getElementById("{elem_id}");
    if(!el) return;
    el.textContent=fmt(total);
    if(total>0) setTimeout(function(){{ total--; tick(); }}, 1000);
  }}
  tick();
}})();
</script>
            """,
            height=70
        )

        # ---- Keep-alive ----
        try:
            if not dentro_pregao(now):
                APP_URL = "https://losscurtissimo.streamlit.app"
                intervalo_ping = 15 * 60
                ultimo_ping = st.session_state.get("ultimo_ping_keepalive")
                if isinstance(ultimo_ping, str):
                    try:
                        ultimo_ping = datetime.datetime.fromisoformat(ultimo_ping)
                    except Exception:
                        ultimo_ping = None
                if not ultimo_ping or (now - ultimo_ping).total_seconds() > intervalo_ping:
                    requests.get(APP_URL, timeout=5)
                    st.session_state["ultimo_ping_keepalive"] = now.isoformat()
                    st.session_state.log_monitoramento.append(
                        f"{now.strftime('%H:%M:%S')} | 🔄 Keep-alive ping enviado para {APP_URL}"
                    )
        except Exception as e:
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | ⚠️ Erro no keep-alive: {e}"
            )

        if faltam > 3600:
            sleep_segundos = 900
        elif faltam > 600:
            sleep_segundos = 300
        else:
            sleep_segundos = 180

# Limita crescimento do log
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]

with log_container:
    render_log_html(st.session_state.log_monitoramento, selected_tickers, max_lines=250)

# -----------------------------
# 🧪 Debug / Backup do estado (JSON)
# -----------------------------
with st.expander("🧪 Debug / Backup do estado (JSON)", expanded=False):
    st.caption(f"Arquivo: `{SAVE_PATH}`")
    try:
        if os.path.exists(SAVE_PATH):
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                state_preview = json.load(f)
            st.json(state_preview)
            st.download_button(
                "⬇️ Baixar state_loss_curtissimo.json",
                data=json.dumps(state_preview, ensure_ascii=False, indent=2),
                file_name="state_loss_curtissimo.json",
                mime="application/json",
            )
        else:
            st.info("Ainda não existe arquivo salvo.")
    except Exception as e:
        st.error(f"Erro ao exibir JSON: {e}")

# Salva antes de dormir
salvar_estado()

# Reexecução
time.sleep(sleep_segundos)
st.rerun()

