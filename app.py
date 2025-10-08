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

TZ = ZoneInfo("Europe/Lisbon")                    # Lisboa (DST automático)
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)   # 14:00 Lisboa
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)   # 21:00 Lisboa
INTERVALO_VERIFICACAO = 300                       # 5 min
TEMPO_ACUMULADO_MAXIMO = 900                      # 15 min (mude p/ 1500=25min se quiser)
LOG_MAX_LINHAS = 1000                             # limite de linhas do log

# Paleta de cores (rotaciona entre tickers)
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

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

def enviar_notificacao(destinatario, assunto, corpo, remetente, senha_ou_token, token_telegram, chat_ids):
    """Envia e-mail e Telegram"""
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)
    async def send_telegram():
        try:
            bot = Bot(token=token_telegram)
            for chat_id in chat_ids:
                await bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")
        except Exception as e:
            print(f"Erro Telegram: {e}")
    asyncio.run(send_telegram())

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol):
    tk = Ticker(ticker_symbol)
    # tenta preço em tempo real; fallback: fechamento
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
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {msg_op} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    # dica: coloque em st.secrets["gmail_app_password"]
    senha_ou_token = st.secrets.get("gmail_app_password", "anoe gegm boqj ldzo")
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {msg_op} em {ticker_symbol_sem_ext}"
    chat_ids = [st.secrets.get("telegram_chat_id", "-1002533284493")]
    token_telegram = st.secrets.get("telegram_token", "6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
    enviar_notificacao(destinatario, assunto, mensagem, remetente, senha_ou_token, token_telegram, chat_ids)
    return mensagem

async def testar_telegram():
    token = st.secrets.get("telegram_token", "6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
    chat = st.secrets.get("telegram_chat_id", "-1002533284493")
    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat, text="✅ Teste de alerta CLUBE funcionando!")
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

# ---------- LOG: cor por ticker + box rolável + ordem decrescente ----------
def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def color_for_ticker(ticker: str) -> str:
    ensure_color_map()
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]

TICKER_PAT = re.compile(r"\b([A-Z]{4,6}\d{0,2})\.SA\b")  # ex: PETR4.SA, ITUB4.SA
PLAIN_TICKER_PAT = re.compile(r"\b([A-Z]{4,6}\d{0,2})\b")  # ex: PETR4, VALE3

def extract_ticker(line: str) -> str | None:
    m = TICKER_PAT.search(line)
    if m:
        return m.group(1)
    # tenta pegar padrão simples quando não tem .SA
    m2 = PLAIN_TICKER_PAT.search(line)
    return m2.group(1) if m2 else None

def render_log_html(lines: list[str], selected_tickers: list[str] | None, max_lines: int = 200):
    """Renderiza o log com cores por ticker, box rolável e ordem decrescente, com fade suave."""
    if not lines:
        st.write("—")
        return
    # Pega últimas N e inverte (mais novo no topo)
    subset = lines[-max_lines:][::-1]

    # filtra se necessário
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
        animation: fadein .18s ease-in;
      }
      @keyframes fadein { from {opacity:.35} to {opacity:1} }
      .log-line{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 13px;
        line-height: 1.35;
        margin: 2px 0;
        color: #e5e7eb;
        display: flex;
        align-items: baseline;
        gap: 8px;
      }
      .ts{ color:#9ca3af; min-width:64px; text-align:right; }
      .badge{
        display:inline-block; padding:1px 8px; font-size:12px; border-radius:9999px; color:white;
      }
      .msg{ white-space: pre-wrap; }
    </style>
    """
    html = [css, "<div class='log-card'>"]
    for l in subset:
        # quebra "HH:MM:SS | resto"
        if " | " in l:
            ts, rest = l.split(" | ", 1)
        else:
            ts, rest = "", l
        tk = extract_ticker(l)
        badge_html = ""
        if tk:
            color = color_for_ticker(tk)
            badge_html = f"<span class='badge' style='background:{color}'>{tk}</span>"
        html.append(f"<div class='log-line'><span class='ts'>{ts}</span>{badge_html}<span class='msg'>{rest}</span></div>")
    html.append("</div>")
    st.markdown("\n".join(html), unsafe_allow_html=True)

# -----------------------------
# ESTADOS GLOBAIS
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos"] else []

# Modo edição/pausa (começa pausado para cadastrar vários tickers)
if "pausado" not in st.session_state:
    st.session_state.pausado = True

# Último estado de pausa (para logar apenas quando muda)
if "ultimo_estado_pausa" not in st.session_state:
    st.session_state.ultimo_estado_pausa = None

# Pontos de disparo (para marcar ⭐ no gráfico)
if "disparos" not in st.session_state:
    st.session_state.disparos = {}  # { 'TICKER': [(datetime, preco), ...] }

ensure_color_map()

# -----------------------------
# SIDEBAR - CONFIGURAÇÕES
# -----------------------------
st.sidebar.header("⚙️ Configurações")

# Botão único de teste do Telegram (sem mostrar token/chat)
if st.sidebar.button("📤 Testar Envio Telegram"):
    st.sidebar.info("Enviando mensagem de teste (usando st.secrets)...")
    ok, erro = asyncio.run(testar_telegram())
    if ok:
        st.sidebar.success("✅ Mensagem enviada com sucesso!")
    else:
        st.sidebar.error(f"❌ Falha: {erro}")

st.sidebar.checkbox("⏸️ Pausar monitoramento (modo edição)", key="pausado")

st.sidebar.header("📜 Histórico de Alertas")
if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta ainda.")
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

# Filtro por ticker no LOG (de volta)
tickers_existentes = sorted(set([a["ticker"] for a in st.session_state.ativos])) if st.session_state.ativos else []
selected_tickers = st.sidebar.multiselect("Filtrar tickers no log", tickers_existentes, default=[])

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
now = agora_lx()
st.title("📈 CLUBE - COMPRA E VENDA")
st.caption(
    f"Agora: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} — "
    f"{'🟩 Dentro do pregão' if dentro_pregao(now) else '🟥 Fora do pregão'}"
)
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
log_container = st.empty()  # HTML estilizado (rolável, cores, descendente)

# -----------------------------
# CICLO ÚNICO + REEXECUÇÃO AUTOMÁTICA
# -----------------------------
sleep_segundos = 60  # padrão fora do pregão / pausado

# Logar mudança de estado de pausa apenas quando o estado muda (sem spam e dentro do box)
if st.session_state.pausado != st.session_state.ultimo_estado_pausa:
    st.session_state.ultimo_estado_pausa = st.session_state.pausado
    st.session_state.log_monitoramento.append(
        f"{now.strftime('%H:%M:%S')} | {'⏸ Pausado (modo edição).' if st.session_state.pausado else '▶️ Retomado.'}"
    )

if st.session_state.pausado:
    # Só mantém a página viva; não loga nada fora do box (acima já logamos a mudança)
    pass
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
        tickers_para_remover = []  # <- para tirar da busca após disparo
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
                    alerta_msg = notificar_preco_alvo_alcancado(tk_full, preco_alvo, preco_atual, operacao_atv)
                    st.warning(alerta_msg)
                    st.session_state.historico_alertas.append({
                        "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "ticker": t,
                        "operacao": operacao_atv,
                        "preco_alvo": preco_alvo,
                        "preco_atual": preco_atual
                    })

                    # ⭐ guarda o ponto do disparo p/ marcar no gráfico
                    st.session_state.disparos.setdefault(t, []).append((now, preco_atual))

                    # marca para remover da busca após o loop
                    tickers_para_remover.append(t)

                    # (não precisa resetar contadores se vamos remover)
                else:
                    # ainda em contagem; nada a fazer
                    pass
            else:
                if st.session_state.em_contagem[t]:
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🔴 Fora da zona"
                    st.session_state.log_monitoramento.append(
                        f"❌ {t} saiu da zona de preço alvo. Contagem reiniciada."
                    )

        # Remove da busca os tickers disparados
        if tickers_para_remover:
            st.session_state.ativos = [a for a in st.session_state.ativos if a["ticker"] not in tickers_para_remover]
            for t in tickers_para_remover:
                st.session_state.tempo_acumulado.pop(t, None)
                st.session_state.em_contagem.pop(t, None)
                st.session_state.status[t] = "✅ Disparado (removido)"
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | 🧹 Removidos após disparo: {', '.join(tickers_para_remover)}"
            )

        # Gráfico: linhas por ticker (cor consistente) + marcadores de disparo ⭐
        fig = go.Figure()
        for t, dados in st.session_state.precos_historicos.items():
            if len(dados) > 1:
                xs, ys = zip(*dados)
                fig.add_trace(go.Scatter(
                    x=xs, y=ys,
                    mode="lines+markers",
                    name=t,
                    line=dict(color=color_for_ticker(t), width=2)
                ))

        # ⭐ marcadores de disparo
        for t, pontos in st.session_state.disparos.items():
            if not pontos:
                continue
            xs, ys = zip(*pontos)
            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="markers",
                name=f"Disparo {t}",
                marker=dict(
                    symbol="star",
                    size=12,
                    color=color_for_ticker(t),       # mesma cor do ticker
                    line=dict(width=2, color="white")
                ),
                hovertemplate=(
                    f"{t}<br>%{{x|%Y-%m-%d %H:%M:%S}}"
                    "<br><b>DISPARO</b>"
                    "<br>Preço: R$ %{y:.2f}<extra></extra>"
                ),
            ))

        fig.update_layout(
            title="📉 Evolução dos Preços (com disparos ⭐)",
            xaxis_title="Tempo", yaxis_title="Preço (R$)",
            legend_title="Legenda",
            template="plotly_dark"
        )
        grafico.plotly_chart(fig, use_container_width=True)

        sleep_segundos = INTERVALO_VERIFICACAO  # 5 min

    else:
        # Fora do pregão: countdown simples no log (apenas uma linha por ciclo)
        faltam = segundos_ate_abertura(now)
        st.session_state.log_monitoramento.append(
            f"{now.strftime('%H:%M:%S')} | ⏸ Fora do pregão. Abre em ~{faltam}s."
        )
        sleep_segundos = min(60, max(1, faltam))

# Limita crescimento do log (memória)
if len(st.session_state.log_monitoramento) > LOG_MAX_LINHAS:
    st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]

# Renderiza LOG estilizado (descendente, cores, box rolável, filtro por ticker)
with log_container:
    render_log_html(st.session_state.log_monitoramento, selected_tickers, max_lines=250)

# Dorme e reexecuta (server-side; não depende do navegador)
time.sleep(sleep_segundos)
st.rerun()






