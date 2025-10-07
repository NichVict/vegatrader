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

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)
INTERVALO_VERIFICACAO = 300   # 5 minutos
TEMPO_ACUMULADO_MAXIMO = 1500 # 25 minutos

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
    """Envia e-mail e Telegram (assíncrono, compatível com python-telegram-bot v20+)"""
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)

    async def send_telegram():
        try:
            bot = Bot(token=token_telegram)
            for chat_id in chat_ids:
                await bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")
        except Exception as e:
            print(f"Erro ao enviar Telegram: {e}")

    asyncio.run(send_telegram())

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
        f"Operação de {mensagem_operacao} em {ticker_symbol_sem_ext} ativada! "
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )

    remetente = "avisoscanal1milhao@gmail.com"
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

# -----------------------------
# ESTADO DA APLICAÇÃO
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado", "em_contagem", "status"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status"] else []

# -----------------------------
# BARRA LATERAL - HISTÓRICO
# -----------------------------
st.sidebar.header("📜 Histórico de Alertas")

if st.session_state.historico_alertas:
    for alerta in reversed(st.session_state.historico_alertas):
        st.sidebar.write(f"**{alerta['ticker']}** - {alerta['operacao'].upper()}")
        st.sidebar.caption(f"{alerta['hora']} | Alvo: {alerta['preco_alvo']:.2f} | Atual: {alerta['preco_atual']:.2f}")
else:
    st.sidebar.info("Nenhum alerta enviado ainda.")

if st.sidebar.button("🧹 Limpar histórico"):
    st.session_state.historico_alertas.clear()
    st.sidebar.success("Histórico limpo!")

# -----------------------------
# INTERFACE PRINCIPAL
# -----------------------------
st.title("📈 CLUBE - COMPRA E VENDA")
st.write("Cadastre tickers, operações e preços alvo. Depois inicie o monitoramento automático.")

col1, col2, col3 = st.columns([1, 1, 1])
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
        st.success(f"Ativo {ticker} adicionado com sucesso!")

# -----------------------------
# TESTE TELEGRAM
# -----------------------------
st.subheader("🤖 Teste do Telegram")
token_telegram = st.text_input("Token do Bot", type="password",
                               value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
chat_id_teste = st.text_input("Chat ID para teste", value="-1002533284493")

if st.button("📤 Enviar mensagem de teste"):
    st.info("Enviando mensagem de teste...")
    ok, erro = asyncio.run(testar_telegram(token_telegram, chat_id_teste))
    if ok:
        st.success("✅ Mensagem enviada com sucesso ao Telegram!")
    else:
        st.error(f"❌ Falha ao enviar: {erro}")

# -----------------------------
# EXIBIÇÃO INICIAL DA TABELA
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

# -----------------------------
# LOG DE MONITORAMENTO
# -----------------------------
st.subheader("🕒 Log de Monitoramento")
log_box = st.empty()

# -----------------------------
# LOOP DE MONITORAMENTO
# -----------------------------
if st.button("🚀 Iniciar monitoramento"):
    if not st.session_state.ativos:
        st.error("Adicione pelo menos um ativo antes de iniciar.")
    else:
        st.success("Monitoramento iniciado...")

        while True:
            now = datetime.datetime.now()
            horario = now.time()

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

            # Monitoramento ativo
            if HORARIO_INICIO_PREGAO <= horario <= HORARIO_FIM_PREGAO:
                for ativo in st.session_state.ativos:
                    t = ativo["ticker"]
                    preco_alvo = ativo["preco"]
                    operacao = ativo["operacao"]
                    ticker_symbol_full = f"{t}.SA"

                    try:
                        preco_atual = obter_preco_atual(ticker_symbol_full)
                    except Exception as e:
                        st.session_state.log_monitoramento.append(f"Erro ao buscar {t}: {e}")
                        continue

                    msg = f"{now.strftime('%H:%M:%S')} | {ticker_symbol_full}: R$ {preco_atual:.2f}"
                    st.session_state.log_monitoramento.append(msg)
                    log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))

                    condicao_atingida = (
                        (operacao == "compra" and preco_atual >= preco_alvo) or
                        (operacao == "venda" and preco_atual <= preco_alvo)
                    )

                    if condicao_atingida:
                        st.session_state.status[t] = "🟡 Em contagem"
                        if not st.session_state.em_contagem[t]:
                            st.session_state.em_contagem[t] = True
                            st.session_state.tempo_acumulado[t] = 0
                            st.session_state.log_monitoramento.append(
                                f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando contagem...")

                        st.session_state.tempo_acumulado[t] += INTERVALO_VERIFICACAO
                        st.session_state.log_monitoramento.append(
                            f"⏱ {t}: {st.session_state.tempo_acumulado[t]}s acumulados")

                        if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                            alerta_msg = notificar_preco_alvo_alcancado(
                                ticker_symbol_full, preco_alvo, preco_atual, operacao, token_telegram)
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
                                f"❌ {t} saiu da zona de preço alvo. Contagem reiniciada.")

                tabela_status.table(df)
                log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))
                time.sleep(INTERVALO_VERIFICACAO)
            else:
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | ⏸ Fora do horário de pregão.")
                log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))
                time.sleep(300)



