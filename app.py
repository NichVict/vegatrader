import os
import time
import json
import logging
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from yahooquery import Ticker

import asyncio
from telegram import Bot

# --- (opcional) Postgres ---
DB_ENABLED = False
try:
    import psycopg2
    DB_ENABLED = True
except Exception:
    DB_ENABLED = False

# ---------------------------
# Configurações via ENV VARS
# ---------------------------
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Lisbon"))
MARKET_START = os.getenv("MARKET_START", "14:00:00")  # hh:mm:ss
MARKET_END = os.getenv("MARKET_END", "21:00:00")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))   # segundos (5 min)
HOLD_TIME = int(os.getenv("HOLD_TIME", "1500"))            # segundos (25 min)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if cid.strip()]

SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_TO  = os.getenv("EMAIL_TO", "docs1milhao@gmail.com")
EMAIL_FROM = SMTP_USER

DATABASE_URL = os.getenv("DATABASE_URL", "")  # ex: postgres://user:pass@host:5432/db
TICKERS_JSON = os.getenv("TICKERS_JSON", "")  # fallback quando DB não está presente

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("worker")

# ---------------------------
# E-mail & Telegram
# ---------------------------
def enviar_email(destinatario: str, assunto: str, corpo: str, remetente: str, senha_ou_token: str):
    if not remetente or not senha_ou_token:
        log.warning("SMTP não configurado. Pulei envio de e-mail.")
        return
    mensagem = MIMEMultipart()
    mensagem["From"] = remetente
    mensagem["To"] = destinatario
    mensagem["Subject"] = assunto
    mensagem.attach(MIMEText(corpo, "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.starttls()
        servidor.login(remetente, senha_ou_token)
        servidor.send_message(mensagem)

async def _send_telegram_async(token: str, chat_ids: list[str], text: str):
    if not token or not chat_ids:
        log.warning("Telegram não configurado. Pulei envio de Telegram.")
        return
    bot = Bot(token=token)
    for cid in chat_ids:
        try:
            await bot.send_message(chat_id=cid, text=text)
        except Exception as e:
            log.error(f"Falha ao enviar Telegram para {cid}: {e}")

def enviar_notificacao(assunto: str, corpo: str):
    # e-mail
    try:
        enviar_email(EMAIL_TO, assunto, corpo, EMAIL_FROM, SMTP_PASS)
        log.info("E-mail enviado.")
    except Exception as e:
        log.error(f"Erro ao enviar e-mail: {e}")

    # telegram
    try:
        asyncio.run(_send_telegram_async(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, corpo + "\n\nRobot 1milhão Invest."))
        log.info("Telegram enviado.")
    except Exception as e:
        log.error(f"Erro ao enviar Telegram: {e}")

# ---------------------------
# Preço (Yahoo)
# ---------------------------
@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=60),
       retry=retry_if_exception_type(requests.exceptions.HTTPError))
def obter_preco_atual(ticker_symbol: str) -> float:
    tk = Ticker(ticker_symbol)
    # 1) tenta preço em tempo real do Yahoo (quando disponível)
    try:
        p = tk.price[ticker_symbol]
        val = p.get("regularMarketPrice")
        if val is not None:
            return float(val)
    except Exception:
        pass
    # 2) fallback: último fechamento
    try:
        hist = tk.history(period="1d")["close"]
        return float(hist.iloc[-1])
    except Exception as e:
        if "429" in str(e):
            raise requests.exceptions.HTTPError("429 Too Many Requests")
        raise

# ---------------------------
# Leitura de Ativos (DB ou ENV)
# ---------------------------
def db_connect():
    if not (DB_ENABLED and DATABASE_URL):
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        conn.autocommit = True
        return conn
    except Exception as e:
        log.error(f"Falha conectar ao Postgres: {e}")
        return None

def db_init(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS ativos (
            id SERIAL PRIMARY KEY,
            ticker TEXT NOT NULL,
            operacao TEXT NOT NULL CHECK (operacao IN ('compra','venda')),
            preco_alvo NUMERIC NOT NULL,
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ativos_ativo ON ativos(ativo);")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_state (
            ticker TEXT PRIMARY KEY,
            em_contagem BOOLEAN DEFAULT FALSE,
            tempo_acumulado INTEGER DEFAULT 0,
            last_price NUMERIC,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """)

def db_fetch_ativos(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, operacao, preco_alvo FROM ativos WHERE ativo = TRUE;")
        rows = cur.fetchall()
    ativos = []
    for t, op, preco in rows:
        t_clean = t.strip().upper()
        if not t_clean.endswith(".SA"):
            t_clean = f"{t_clean}.SA"
        ativos.append({"ticker": t_clean, "operacao": op.lower(), "preco": float(preco)})
    return ativos

def db_update_state(conn, ticker: str, em_contagem: bool, tempo_acumulado: int, last_price: float):
    with conn.cursor() as cur:
        cur.execute("""
        INSERT INTO monitor_state (ticker, em_contagem, tempo_acumulado, last_price, updated_at)
        VALUES (%s,%s,%s,%s,now())
        ON CONFLICT (ticker) DO UPDATE
        SET em_contagem = EXCLUDED.em_contagem,
            tempo_acumulado = EXCLUDED.tempo_acumulado,
            last_price = EXCLUDED.last_price,
            updated_at = now();
        """, (ticker, em_contagem, tempo_acumulado, last_price))

def parse_env_tickers():
    if not TICKERS_JSON:
        return []
    try:
        data = json.loads(TICKERS_JSON)
        ativos = []
        for a in data:
            t = str(a["ticker"]).strip().upper()
            if not t.endswith(".SA"):
                t = f"{t}.SA"
            ativos.append({"ticker": t, "operacao": a["operacao"].lower(), "preco": float(a["preco"])})
        return ativos
    except Exception as e:
        log.error(f"TICKERS_JSON inválido: {e}")
        return []

# ---------------------------
# Lógica de Monitoramento
# ---------------------------
def dentro_do_pregao(now: dt.datetime) -> bool:
    t = now.time()
    start = dt.time.fromisoformat(MARKET_START)
    end = dt.time.fromisoformat(MARKET_END)
    return start <= t <= end

def main():
    log.info("Iniciando worker contínuo de monitoramento...")

    conn = db_connect()
    if conn:
        db_init(conn)
        log.info("Banco de dados inicializado.")
    else:
        log.info("Rodando SEM banco (usando TICKERS_JSON).")

    # estado em memória
    tempo_acumulado = {}
    em_contagem = {}

    while True:
        now = dt.datetime.now(TZ)

        # Carrega ativos (DB > ENV fallback)
        if conn:
            try:
                ativos = db_fetch_ativos(conn)
            except Exception as e:
                log.error(f"Falha lendo ativos do DB: {e}")
                ativos = parse_env_tickers()
        else:
            ativos = parse_env_tickers()

        if not ativos:
            log.warning("Nenhum ativo configurado. Configure via DB ou TICKERS_JSON. Aguardando...")
            time.sleep(CHECK_INTERVAL)
            continue

        # Inicializa estado de novos tickers
        for a in ativos:
            t = a["ticker"]
            tempo_acumulado.setdefault(t, 0)
            em_contagem.setdefault(t, False)

        if not dentro_do_pregao(now):
            log.info("⏸ Fora do horário de pregão. Aguardando...")
            time.sleep(300)
            continue

        log.info(f"--- Varredura ({len(ativos)} ativos) ---")
        for a in ativos:
            t = a["ticker"]
            op = a["operacao"]
            alvo = a["preco"]

            try:
                preco = obter_preco_atual(t)
                log.info(f"{t}: R$ {preco:.2f}")
            except Exception as e:
                log.error(f"Erro ao obter preço {t}: {e}")
                continue

            cond = (op == "compra" and preco >= alvo) or (op == "venda" and preco <= alvo)

            if cond:
                if not em_contagem[t]:
                    em_contagem[t] = True
                    tempo_acumulado[t] = 0
                    log.info(f"⚠️  {t} atingiu o alvo ({alvo:.2f}). Iniciando contagem...")

                tempo_acumulado[t] += CHECK_INTERVAL
                log.info(f"⏱ {t}: {tempo_acumulado[t]}s acumulados")

                if tempo_acumulado[t] >= HOLD_TIME:
                    # Dispara alerta
                    oper_str = "VENDA A DESCOBERTO" if op == "venda" else "COMPRA"
                    assunto = f"ALERTA: {oper_str} em {t.replace('.SA','')}"
                    corpo = (
                        f"Operação de {oper_str} em {t.replace('.SA','')} ativada!\n"
                        f"Preço alvo: {alvo:.2f} | Preço atual: {preco:.2f}\n\n"
                        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
                    )
                    enviar_notificacao(assunto, corpo)

                    # Reset
                    em_contagem[t] = False
                    tempo_acumulado[t] = 0
            else:
                if em_contagem[t]:
                    em_contagem[t] = False
                    tempo_acumulado[t] = 0
                    log.info(f"❌ {t} saiu da zona de preço alvo. Contagem reiniciada.")

            if conn:
                try:
                    db_update_state(conn, t, em_contagem[t], tempo_acumulado[t], preco)
                except Exception as e:
                    log.error(f"Falha ao atualizar estado no DB ({t}): {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Encerrando worker (KeyboardInterrupt).")
    except Exception as e:
        log.exception(f"Erro fatal no worker: {e}")





