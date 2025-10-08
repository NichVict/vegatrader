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
from zoneinfo import ZoneInfo  # fuso com DST

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")                    # usa Lisboa (horário de verão automático)
HORARIO_INICIO_PREGAO = datetime.time(15, 0, 0)   # 14:00 Lisboa
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)   # 21:00 Lisboa
INTERVALO_VERIFICACAO = 60   # 5 minutos
TEMPO_ACUMULADO_MAXIMO = 900  # 15 minutos (mude para 1500 = 25 min se quiser)

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
    ticker_data = Ticker(ticker_symbol)
    # tenta preço em tempo real; fallback: fechamento
    try:
        p = ticker_data.price.get(ticker_symbol, {}).get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    preco_atual = ticker_data.history(period="1d")["close"].iloc[-1]
    return float(preco_atual)

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao, token_telegram):
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    mensagem_operacao = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {mensagem_operacao} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    # dica: coloque em st.secrets["gmail_app_password"]
    senha_ou_token = "anoe gegm boqj ldzo"
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {mensagem_operacao} em {ticker_symbol_sem_ext}"
    chat_ids = ["-1002533284493"]
    enviar_notificacao(destinatario, assunto, mensagem, remetente, senha_ou_token, token_telegram, chat_ids)
    return mensagem

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

# -----------------------------
# ESTADOS GLOBAIS
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos"] else []

# Modo edição/pausa (permite cadastrar vários tickers antes de monitorar)
if "pausado" not in st.session_state:
    st.session_state.pausado = True  # inicia pausado; desmarque para começar quando quiser

# -----------------------------
# SIDEBAR - CONFIGURAÇÕES E TELEGRAM
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
log_box = st.empty()

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

        sleep_segundos = INTERVALO_VERIFICACAO  # próximo ciclo em 5 min

    else:
        # Fora do pregão: countdown simples no log e aguarda próximo ciclo
        faltam = segundos_ate_abertura(now)
        st.session_state.log_monitoramento.append(
            f"{now.strftime('%H:%M:%S')} | ⏸ Fora do pregão. Abre em ~{faltam}s."
        )
        # Se faltar pouco, acorda mais rápido
        sleep_segundos = min(60, max(1, faltam))

# Atualiza log no fim do ciclo
if st.session_state.log_monitoramento:
    log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))

# Dorme e reexecuta (server-side; não depende do navegador)
time.sleep(sleep_segundos)
st.rerun()





