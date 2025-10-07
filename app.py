import streamlit as st
from yahooquery import Ticker
import datetime
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Bot
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests

# -----------------------------
# Configurações
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(23, 0, 0)
INTERVALO_VERIFICACAO = 300   # 5 minutos
TEMPO_ACUMULADO_MAXIMO = 1500 # 25 minutos

# -----------------------------
# Funções auxiliares
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
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)
    bot = Bot(token=token_telegram)
    for chat_id in chat_ids:
        bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol):
    ticker_data = Ticker(ticker_symbol)
    preco_atual = ticker_data.history(period="1d")["close"].iloc[-1]
    return preco_atual

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao, token_telegram):
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    mensagem_operacao = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {mensagem_operacao} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f}\nPreço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )

    remetente = "avisoscanal1milhao@gmail.com"
    senha_ou_token = "anoe gegm boqj ldzo"
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {mensagem_operacao} em {ticker_symbol_sem_ext}"

    chat_ids = ["-1002533284493"]
    enviar_notificacao(destinatario, assunto, mensagem, remetente, senha_ou_token, token_telegram, chat_ids)
    return mensagem

# -----------------------------
# Estado da aplicação
# -----------------------------
if "ativos" not in st.session_state:
    st.session_state.ativos = []

if "historico_alertas" not in st.session_state:
    st.session_state.historico_alertas = []

if "log_monitoramento" not in st.session_state:
    st.session_state.log_monitoramento = []

# -----------------------------
# Barra lateral (histórico)
# -----------------------------
st.sidebar.header("📜 Histórico de Alertas")

if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta enviado ainda.")

# Botão para limpar histórico
if st.sidebar.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")

# -----------------------------
# Interface principal
# -----------------------------
st.title("📈 CLUBE - COMPRA E VENDA")
st.write("Cadastre tickers, operações e preços alvo. Depois inicie o monitoramento automático.")

# Entradas de ativo
col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    ticker = st.text_input("Ticker (ex: PETR4)").upper()
with col2:
    operacao = st.selectbox("Operação", ["compra", "venda"])
with col3:
    preco = st.number_input("Preço alvo", min_value=0.01, step=0.01)

adicionar = st.button("➕ Adicionar ativo")

if adicionar:
    if not ticker:
        st.error("Digite um ticker válido.")
    else:
        ativo = {"ticker": ticker, "operacao": operacao, "preco": preco}
        st.session_state.ativos.append(ativo)
        st.success(f"Ativo {ticker} adicionado para {operacao.upper()} a R$ {preco:.2f}")

# Exibe tabela de ativos cadastrados
if st.session_state.ativos:
    st.subheader("📋 Ativos cadastrados")
    st.table(st.session_state.ativos)

# Token Telegram
token_telegram = st.text_input("Token do Telegram", type="password",
                               value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")

# Caixa de log do monitoramento
st.subheader("📊 Atualizações de monitoramento")
log_box = st.empty()

# Botão iniciar
iniciar = st.button("🚀 Iniciar monitoramento")

if iniciar:
    if not st.session_state.ativos:
        st.error("Adicione pelo menos um ativo antes de iniciar.")
    else:
        st.success("Monitoramento iniciado...")
        tempo_acumulado = {a["ticker"]: 0 for a in st.session_state.ativos}

        while True:
            now = datetime.datetime.now()
            horario = now.time()

            if HORARIO_INICIO_PREGAO <= horario <= HORARIO_FIM_PREGAO:
                for ativo in st.session_state.ativos:
                    ticker_symbol_full = f"{ativo['ticker']}.SA"
                    preco_alvo = ativo["preco"]
                    operacao = ativo["operacao"]

                    try:
                        preco_atual = obter_preco_atual(ticker_symbol_full)
                    except Exception as e:
                        msg_erro = f"{now.strftime('%H:%M:%S')} | Erro ao buscar {ativo['ticker']}: {e}"
                        st.session_state.log_monitoramento.append(msg_erro)
                        continue

                    msg = f"{now.strftime('%H:%M:%S')} | {ticker_symbol_full}: R$ {preco_atual:.2f}"
                    st.session_state.log_monitoramento.append(msg)

                    # Mostra últimas 20 linhas do log
                    log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))

                    if (operacao == "compra" and preco_atual >= preco_alvo) or (operacao == "venda" and preco_atual <= preco_alvo):
                        tempo_acumulado[ativo["ticker"]] += INTERVALO_VERIFICACAO
                        if tempo_acumulado[ativo["ticker"]] >= TEMPO_ACUMULADO_MAXIMO:
                            alerta_msg = notificar_preco_alvo_alcancado(
                                ticker_symbol_full, preco_alvo, preco_atual, operacao, token_telegram
                            )
                            st.warning(alerta_msg)

                            # Salva no histórico
                            st.session_state.historico_alertas.append({
                                "hora": now.strftime("%Y-%m-%d %H:%M:%S"),
                                "ticker": ativo["ticker"],
                                "operacao": operacao,
                                "preco_alvo": preco_alvo,
                                "preco_atual": preco_atual
                            })

                            tempo_acumulado[ativo["ticker"]] = 0
                    else:
                        tempo_acumulado[ativo["ticker"]] = 0

                time.sleep(INTERVALO_VERIFICACAO)
            else:
                msg = f"{now.strftime('%H:%M:%S')} | ⏸ Fora do horário de pregão. Aguardando..."
                st.session_state.log_monitoramento.append(msg)
                log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))
                time.sleep(300)


