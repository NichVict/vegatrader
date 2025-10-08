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

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")                    # Lisboa (DST ok)
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)   # 14:00
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)   # 21:00
INTERVALO_VERIFICACAO = 300                       # 5 min
TEMPO_ACUMULADO_MAXIMO = 900                      # 15 min (use 1500 p/ 25 min)
LOG_MAX_LINHAS = 120                              # limite de linhas guardadas

# Paleta de cores p/ tickers (rotaciona)
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    msg = MIMEMultipart()
    msg["From"] = remetente
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.starttls()
        servidor.login(remetente, senha_ou_token)
        servidor.send_message(msg)

def enviar_notificacao(destinatario, assunto, corpo, remetente, senha_ou_token, token_telegram, chat_ids):
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)
    async def send_telegram():
        try:
            bot = Bot(token=token_telegram)
            for chat_id in chat_ids:
                await bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")
        except Exception as e:
            print(f"Erro Telegram: {e}")
    asyncio.run(send_telegram())

@retry(stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=1, min=4, max=60),
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

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao, token_telegram):
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    msg = (
        f"Operação de {op} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    senha_ou_token = "anoe gegm boqj ldzo"  # ideal: st.secrets["gmail_app_password"]
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {op} em {ticker_symbol_sem_ext}"
    chat_ids = ["-1002533284493"]
    enviar_notificacao(destinatario, assunto, msg, remetente, senha_ou_token, token_telegram, chat_ids)
    return msg

async def testar_telegram(token_telegram, chat_id):
    try:
        bot = Bot(token=token_telegram)
        await bot.send_message(chat_id=chat_id, text="✅ Teste de alerta CLUBE funcionando!")
        return True, None
    except Exception as e:
        return False, str(e)

def agora_lx():
    return datetime.datetime.now(TZ)

def dentro_pregao(dt_now):
    t = dt_now.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_abertura(dt_now):
    hoje_abre = dt_now.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=0, second=0, microsecond=0)
    hoje_fecha = dt_now.replace(hour=HORARIO_FIM_PREGAO.hour, minute=0, second=0, microsecond=0)
    if dt_now < hoje_abre:
        return int((hoje_abre - dt_now).total_seconds())
    elif dt_now > hoje_fecha:
        amanha_abre = hoje_abre + datetime.timedelta(days=1)
        return int((amanha_abre - dt_now).total_seconds())
    else:
        return 0

# ---- util p/ LOG colorido ----
def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def color_for_ticker(ticker: str) -> str:
    ensure_color_map()
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]

TICKER_PAT = re.compile(r"\b([A-Z]{4,6}\d{0,2})\.SA\b")  # ex: PETR4.SA, ITUB4.SA, VALE3.SA

def extract_ticker(line: str) -> str | None:
    m = TICKER_PAT.search(line)
    if m:
        return m.group(1)
    # também tenta padrões do tipo "⏱ ITUB4:" ou "⚠️ ITUB4"
    m2 = re.search(r"\b([A-Z]{4,6}\d{0,2})\b(?=:| |$)", line)
    if m2:
        return m2.group(1)
    return None

def render_log(lines: list[str], selected_tickers: list[str] | None, max_lines: int = 50):
    """Renderiza o log em ordem decrescente, com cor por ticker."""
    if not lines:
        st.write("—")
        return
    # limita e reverte (mais novo no topo)
    subset = lines[-max_lines:][::-1]

    # filtra por ticker se necessário
    rendered = []
    for l in subset:
        tk = extract_ticker(l)
        if selected_tickers and tk and tk not in selected_tickers:
            continue
        rendered.append((l, tk))

    if not rendered:
        st.write("—")
        return

    # CSS leve
    css = """
    <style>
      .log-line{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
                font-size: 13px; margin: 2px 0; line-height: 1.35;}
      .ts{color:#9ca3af; margin-right:6px;}
      .badge{display:inline-block; padding:1px 6px; font-size:12px; border-radius:9999px; color:white; margin-right:6px;}
      .msg{color:#e5e7eb;}
      .wrap{white-space: pre-wrap;}
    </style>
    """
    html = [css]
    for l, tk in rendered:
        # quebra "HH:MM:SS | resto"
        if " | " in l:
            ts, rest = l.split(" | ", 1)
        else:
            ts, rest = "", l

        # badge do ticker
        badge_html = ""
        if tk:
            c = color_for_ticker(tk)
            badge_html = f"<span class='badge' style='background:{c}'>{tk}</span>"

        html.append(f"<div class='log-line wrap'><span class='ts'>{ts}</span>{badge_html}<span class='msg'>{rest}</span></div>")

    st.markdown("\n".join(html), unsafe_allow_html=True)

# -----------------------------
# ESTADOS GLOBAIS
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos"] else []

# Modo edição/pausa
if "pausado" not in st.session_state:
    st.session_state.pausado = True  # comece pausado para cadastrar com calma

ensure_color_map()

# -----------------------------
# SIDEBAR - CONFIGURAÇÕES
# -----------------------------
st.sidebar.header("⚙️ Configurações")
token_telegram = st.sidebar.text_input("Token do Bot Telegram", type="password",
                                       value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
chat_id_teste = st.sidebar.text_input("Chat ID (grupo ou usuário)", value="-1002533284493")
st.sidebar.checkbox("⏸️ Pausar monitoramento (modo edição)", key="pausado")

if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram(token_telegram, chat_id_teste))
    if ok:
        st.sidebar.success("✅ Mensagem enviada com sucesso!")
    else:
        st.sidebar.error(f"❌ Falha: {erro}")

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")
if st.sidebar.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")

# --- Filtro do LOG + limpar LOG
tickers_existentes = [a["ticker"] for a in st.session_state.ativos] if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", sorted(set(tickers_existentes)))
if st.sidebar.button("🧽 Limpar LOG"):
    st.session_state.log_monitoramento.clear()
    st.sidebar.success("Log limpo!")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CLUBE - COMPRA E VENDA")
st.caption(f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
           f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}")
st.write("Cadastre tickers, operações e preços alvo. O monitor roda automaticamente no horário do pregão (ou quando você despausar).")

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
        st.success(f"Ativo {ticker} adicionado com sucesso!")

# -----------------------------
# STATUS + GRÁFICO + LOG
# -----------------------------
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()

if st.session_state.ativos:
    data = []
    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        preco_atual = "-"
        try:
            preco_atual = obter_preco_atual(f"{t}.SA")
        except:
            pass
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
    df = pd.DataFrame(data)
    tabela_status.table(df)
else:
    st.info("Nenhum ativo cadastrado ainda.")

st.subheader("📉 Gráfico em Tempo Real dos Preços")
grafico = st.empty()

st.subheader("🕒 Log de Monitoramento")
log_container = st.empty()  # renderizamos em HTML estilizado

# -----------------------------
# CICLO ÚNICO + REEXECUÇÃO AUTOMÁTICA
# -----------------------------
sleep_segundos = 60  # padrão fora do pregão / pausado

# Modo edição/pausa: não monitora; só mantém a página viva
if st.session_state.pausado:
    st.session_state.log_monitoramento.append(
        f"{now.strftime('%H:%M:%S')} | ⏸ Pausado (modo edição)."
    )
else:
    if dentro_pregao(now):
        # 1) Atualiza tabela/gráfico e monitora
        data = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            st.session_state.em_contagem.setdefault(t, False)
            st.session_state.status.setdefault(t, "🟢 Monitorando")

            preco_atual = "-"
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")

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
            tabela_status.table(pd.DataFrame(data))

        # Lógica por ativo
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            preco_alvo = ativo["preco"]
            operacao = ativo["operacao"]
            tk_full = f"{t}.SA"

            try:
                preco_atual = obter_preco_atual(tk_full)
            except Exception as e:
                st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")
                continue

            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | {tk_full}: R$ {preco_atual:.2f}")

            condicao = (
                (operacao == "compra" and preco_atual >= preco_alvo) or
                (operacao == "venda" and preco_atual <= preco_alvo)
            )

            if condicao:
                st.session_state.status[t] = "🟡 Em contagem"
                if not st.session_state.em_contagem[t]:
                    st.session_state.em_contagem[t] = True
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.log_monitoramento.append(
                        f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando contagem..."
                    )
                st.session_state.tempo_acumulado[t] += INTERVALO_VERIFICACAO
                st.session_state.log_monitoramento.append(
                    f"⏱ {t}: {st.session_state.tempo_acumulado[t]}s acumulados"
                )

                if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                    alerta_msg = notificar_preco_alvo_alcancado(tk_full, preco_alvo, preco_atual, operacao, token_telegram)
                    st.warning(alerta_msg)
                    st.session_state.historico_alertas.append({
                        "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "ticker": t,
                        "operacao": operacao,
                        "preco_alvo": preco_alvo,
                        "preco_atual": preco_atual
                    })
                    st.session_state.status[t] = "🟢 Monitorando"
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
            else:
                if st.session_state.em_contagem[t]:
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🔴 Fora da zona"
                    st.session_state.log_monitoramento.append(
                        f"❌ {t} saiu da zona de preço alvo. Contagem reiniciada."
                    )

        # Gráfico por status
        fig = go.Figure()
        for t, dados in st.session_state.precos_historicos.items():
            if len(dados) > 1:
                tempos, precos = zip(*dados)
                status = st.session_state.status.get(t, "🟢 Monitorando")
                cor = "green" if "🟢" in status else "orange" if "🟡" in status else "red"
                fig.add_trace(go.Scatter(x=tempos, y=precos, mode="lines+markers", name=t, line=dict(color=cor)))
        fig.update_layout(title="📉 Evolução dos Preços Monitorados",
                          xaxis_title="Tempo", yaxis_title="Preço (R$)",
                          legend_title="Ticker")
        grafico.plotly_chart(fig, use_container_width=True)

        sleep_segundos = INTERVALO_VERIFICACAO  # 5 min

    else:
        # Fora do pregão: countdown simples no log
        faltam = segundos_ate_abertura(now)
        st.session_state.log_monitoramento.append(
            f"{now.strftime('%H:%M:%S')} | ⏸ Fora do pregão. Abre em ~{faltam}s."
        )
        sleep_segundos = min(60, max(1, faltam))

# Limita crescimento do log (memória)
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]

# Renderiza LOG estilizado (decrescente + cores + filtro)
with log_container:
    render_log(st.session_state.log_monitoramento, selected_tickers, max_lines=60)

# Dorme e reexecuta (server-side; não depende do navegador)
time.sleep(sleep_segundos)
st.rerun()






