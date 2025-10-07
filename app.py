#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import streamlit as st
from yahooquery import Ticker
import datetime
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from telegram import Bot
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import re

# --- Configurações ---
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(23, 0, 0)
INTERVALO_VERIFICACAO = 300
TEMPO_ACUMULADO_MAXIMO = 1500

# --- Funções auxiliares ---

def email_valido(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token):
    mensagem = MIMEMultipart()
    mensagem['From'] = remetente
    mensagem['To'] = destinatario
    mensagem['Subject'] = assunto
    mensagem.attach(MIMEText(corpo, 'plain'))

    with smtplib.SMTP('smtp.gmail.com', 587) as servidor_smtp:
        servidor_smtp.starttls()
        servidor_smtp.login(remetente, senha_ou_token)
        servidor_smtp.send_message(mensagem)

def enviar_notificacao(destinatario, assunto, corpo, remetente, senha_ou_token, token_telegram, chat_ids):
    enviar_email(destinatario, assunto, corpo, remetente, senha_ou_token)
    bot = Bot(token=token_telegram)
    for chat_id in chat_ids:
        bot.send_message(chat_id=chat_id, text=f"{corpo}\n\nRobot 1milhão Invest.")

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type(requests.exceptions.HTTPError)
)
def obter_preco_atual(ticker_symbol):
    ticker_data = Ticker(ticker_symbol)
    preco_atual = ticker_data.history(period='1d')['close'].iloc[-1]
    return preco_atual

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao, token_telegram):
    ticker_symbol_sem_ext = ticker_symbol.replace('.SA', '')
    mensagem_operacao = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {mensagem_operacao} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f}\nPreço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )

    remetente = 'avisoscanal1milhao@gmail.com'
    senha_ou_token = 'anoe gegm boqj ldzo'
    destinatario = 'docs1milhao@gmail.com'
    assunto = f"ALERTA: {mensagem_operacao} em {ticker_symbol_sem_ext}"

    chat_ids = ['-1002533284493']
    enviar_notificacao(destinatario, assunto, mensagem, remetente, senha_ou_token, token_telegram, chat_ids)
    return mensagem

# --- Interface Streamlit ---

st.title("📈 CLUBE - COMPRA E VENDA")
st.write("Monitore tickers e receba alertas automáticos por e-mail e Telegram.")

token_telegram = st.text_input("Token do Telegram:", type="password", value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
ticker_symbol = st.text_input("Ticker (ex: PETR4):")
operacao = st.selectbox("Operação:", ["compra", "venda"])
preco_alvo = st.number_input("Preço Alvo:", min_value=0.01, step=0.01)
executar = st.button("Iniciar Monitoramento")

output = st.empty()

if executar:
    if not ticker_symbol:
        st.error("Por favor, digite um ticker.")
    else:
        ticker_symbol_full = f"{ticker_symbol}.SA"
        st.success(f"Monitorando {ticker_symbol_full} para {operacao.upper()} com preço alvo {preco_alvo:.2f}")
        tempo_acumulado = 0

        while True:
            now = datetime.datetime.now()
            horario = now.time()

            if HORARIO_INICIO_PREGAO <= horario <= HORARIO_FIM_PREGAO:
                try:
                    preco_atual = obter_preco_atual(ticker_symbol_full)
                except Exception as e:
                    output.write(f"Erro ao buscar preço: {e}")
                    time.sleep(60)
                    continue

                output.write(f"{now.strftime('%H:%M:%S')} | {ticker_symbol_full}: R$ {preco_atual:.2f}")

                if (operacao == "compra" and preco_atual >= preco_alvo) or (operacao == "venda" and preco_atual <= preco_alvo):
                    tempo_acumulado += INTERVALO_VERIFICACAO
                    if tempo_acumulado >= TEMPO_ACUMULADO_MAXIMO:
                        mensagem = notificar_preco_alvo_alcancado(ticker_symbol_full, preco_alvo, preco_atual, operacao, token_telegram)
                        st.warning(mensagem)
                        break
                else:
                    tempo_acumulado = 0

                time.sleep(INTERVALO_VERIFICACAO)
            else:
                output.write("⏸ Fora do horário de pregão. Aguardando...")
                time.sleep(300)

