# -*- coding: utf-8 -*-
"""
monitor.py — motor central dos 6 robôs de monitoramento
Executa continuamente, sincronizando com Supabase e notificações Telegram/E-mail.
"""

import os
import time
import datetime
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from yahooquery import Ticker
import pandas as pd
from zoneinfo import ZoneInfo
import toml
import asyncio
from telegram import Bot

# =============================
# CONFIGURAÇÕES BÁSICAS
# =============================
TZ = ZoneInfo("Europe/Lisbon")
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)
INTERVALO_VERIFICACAO = 300  # 5 min
TEMPO_ACUMULADO_MAXIMO = 1500

# =============================
# LEITURA DE SECRETS (automática)
# =============================

def load_secrets():
    """Carrega credenciais automaticamente:
       - Usa secrets.toml se existir (local)
       - Caso contrário, usa variáveis de ambiente (Render)
    """
    if os.path.exists("secrets.toml"):
        with open("secrets.toml", "rb") as f:
            print("✅ Usando credenciais locais (secrets.toml)")
            return toml.load(f)
    else:
        print("☁️ Usando credenciais do ambiente (Render)")
        env_secrets = {}
        for key, value in os.environ.items():
            if any(word in key.lower() for word in [
                "supabase", "telegram", "gmail", "email", "key", "token", "url"
            ]):
                env_secrets[key] = value
        return env_secrets

secrets = load_secrets()
print("✅ Credenciais carregadas com sucesso.\n")

# =============================
# FUNÇÕES GERAIS
# =============================

def agora():
    return datetime.datetime.now(TZ)

def dentro_pregao():
    now = agora().time()
    return HORARIO_INICIO_PREGAO <= now <= HORARIO_FIM_PREGAO

def enviar_email(destinatario, assunto, corpo, remetente, senha):
    if not destinatario or not remetente or not senha:
        print("⚠️ Email não configurado.")
        return
    try:
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Subject"] = remetente, destinatario, assunto
        msg.attach(MIMEText(corpo, "plain"))
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(remetente, senha)
            s.send_message(msg)
        print(f"📧 Email enviado: {destinatario}")
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")

async def enviar_telegram(token, chat_id, mensagem):
    if not token or not chat_id:
        print("⚠️ Telegram não configurado.")
        return
    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode="HTML", disable_web_page_preview=True)
        print(f"📨 Telegram enviado: {chat_id}")
    except Exception as e:
        print(f"❌ Erro Telegram: {e}")

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol):
    try:
        tk = Ticker(ticker_symbol)
        p = tk.price.get(ticker_symbol, {}).get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    return None

# =============================
# CLASSE DO ROBÔ
# =============================

class Robo:
    def __init__(self, nome, supabase_url, supabase_key, tabela, telegram_chat, email_dest):
        self.nome = nome
        self.supabase_url = supabase_url
        self.supabase_key = supabase_key
        self.tabela = tabela
        self.telegram_chat = telegram_chat
        self.email_dest = email_dest
        self.last_run = None

    def log(self, msg):
        print(f"[{self.nome}] {agora().strftime('%H:%M:%S')} | {msg}")

    def salvar_estado(self, state):
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"k": f"{self.nome}_v1", "v": state}
        url = f"{self.supabase_url}/rest/v1/{self.tabela}"
        try:
            requests.post(url, headers=headers, json=payload, timeout=10)
            self.log("💾 Estado salvo na nuvem.")
        except Exception as e:
            self.log(f"⚠️ Erro ao salvar estado: {e}")

    def rodar(self):
        """Simulação de execução de lógica de monitoramento"""
        self.log("🔍 Iniciando verificação...")
        tickers = ["PETR4.SA", "VALE3.SA", "BBDC4.SA"]  # Exemplo fixo; pode ler da Supabase também
        resultados = {}
        for t in tickers:
            preco = obter_preco_atual(t)
            if preco:
                resultados[t] = preco
                self.log(f"{t}: R$ {preco:.2f}")
        self.salvar_estado({"precos": resultados, "timestamp": agora().isoformat()})
        if dentro_pregao():
            msg = f"🤖 {self.nome.upper()} executado — {len(resultados)} ativos atualizados."
            asyncio.run(enviar_telegram(secrets.get("telegram_token"), self.telegram_chat, msg))
        self.last_run = agora()

# =============================
# REGISTRO DOS ROBÔS
# =============================

robos = [
    Robo("curto", secrets.get("supabase_url_curto"), secrets.get("supabase_key_curto"), "kv_state_curto", secrets.get("telegram_chat_id_curto"), secrets.get("email_recipient_curto")),
    Robo("curtissimo", secrets.get("supabase_url_curtissimo"), secrets.get("supabase_key_curtissimo"), "kv_state_curtissimo", secrets.get("telegram_chat_id_curtissimo"), secrets.get("email_recipient_curtissimo")),
    Robo("clube", secrets.get("supabase_url_clube"), secrets.get("supabase_key_clube"), "kv_state_clube", secrets.get("telegram_chat_id_clube"), secrets.get("email_sender")),
    Robo("losscurto", secrets.get("supabase_url_losscurto"), secrets.get("supabase_key_losscurto"), "kv_state_losscurto", secrets.get("telegram_chat_id_losscurto"), secrets.get("email_recipient_losscurto")),
    Robo("losscurtissimo", secrets.get("supabase_url_losscurtissimo"), secrets.get("supabase_key_losscurtissimo"), "kv_state_losscurtissimo", secrets.get("telegram_chat_id_losscurtissimo"), secrets.get("email_recipient_losscurtissimo")),
    Robo("lossclube", secrets.get("supabase_url_lossclube"), secrets.get("supabase_key_lossclube"), "kv_state_lossclube", secrets.get("telegram_chat_id_lossclube"), secrets.get("email_sender")),
]

# =============================
# LOOP PRINCIPAL
# =============================
if __name__ == "__main__":
    print("🚀 Iniciando motor de monitoramento dos 6 robôs...\n")
    while True:
        for robo in robos:
            try:
                robo.rodar()
            except Exception as e:
                robo.log(f"❌ Falha: {e}")
            time.sleep(5)  # pequena pausa entre os robôs
        print(f"⏸️ Aguardando {INTERVALO_VERIFICACAO}s...\n")
        time.sleep(INTERVALO_VERIFICACAO)
