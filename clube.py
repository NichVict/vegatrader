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
import re
import uuid
import streamlit.components.v1 as components
# ==== ADIÇÃO: persistência ====
import json
import os

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

TZ = ZoneInfo("Europe/Lisbon")                    # Lisboa (DST automático)
HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)   # 14:00 Lisboa
HORARIO_FIM_PREGAO    = datetime.time(21, 0, 0)   # 21:00 Lisboa

INTERVALO_VERIFICACAO = 300                       # 5 min
TEMPO_ACUMULADO_MAXIMO = 1500                     # 25 min (1500s)
LOG_MAX_LINHAS = 1000                             # limite de linhas do log

# Paleta de cores (rotaciona entre tickers)
PALETTE = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#22c55e"
]

# ==== PERSISTÊNCIA LOCAL ====
SAVE_DIR = ".streamlit"  # Compatível com Streamlit Cloud
os.makedirs(SAVE_DIR, exist_ok=True)

APP_NAME = "clube_compra_venda"  # Nome único para o script
SAVE_PATH = os.path.join(SAVE_DIR, f"state_{APP_NAME}.json")

def salvar_estado():
    """Salva os dados essenciais do app em JSON."""
    estado = {
        "ativos": st.session_state.get("ativos", []),
        "historico_alertas": st.session_state.get("historico_alertas", []),
        "log_monitoramento": st.session_state.get("log_monitoramento", []),
        "disparos": st.session_state.get("disparos", {}),
        "tempo_acumulado": st.session_state.get("tempo_acumulado", {}),
        "status": st.session_state.get("status", {}),
        "precos_historicos": st.session_state.get("precos_historicos", {}),
        "pausado": st.session_state.get("pausado", False),  # começa ATIVO
        "ultimo_estado_pausa": st.session_state.get("ultimo_estado_pausa", None),
        "ultimo_ping_keepalive": st.session_state.get("ultimo_ping_keepalive", None),
        "avisou_abertura_pregao": st.session_state.get("avisou_abertura_pregao", False),
        "ultimo_update_tempo": st.session_state.get("ultimo_update_tempo", {}),
    }
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, default=str, indent=2)
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar estado: {e}")

def carregar_estado():
    """Restaura os dados do JSON (se existir), sem sobrescrever controles interativos."""
    if os.path.exists(SAVE_PATH):
        try:
            with open(SAVE_PATH, "r", encoding="utf-8") as f:
                estado = json.load(f)

            # 🚫 preserva o valor atual do checkbox, se já existir
            pausado_atual = st.session_state.get("pausado")

            for k, v in estado.items():
                if k == "pausado" and pausado_atual is not None:
                    continue  # mantém o valor clicado
                st.session_state[k] = v

            st.sidebar.info("💾 Estado restaurado com sucesso!")
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

def notificar_preco_alvo_alcancado(ticker_symbol, preco_alvo, preco_atual, operacao):
    """Assinatura simplificada (4 args). Tokens/IDs vêm de st.secrets."""
    ticker_symbol_sem_ext = ticker_symbol.replace(".SA", "")
    msg_op = "VENDA A DESCOBERTO" if operacao == "venda" else "COMPRA"
    mensagem = (
        f"Operação de {msg_op} em {ticker_symbol_sem_ext} ativada!\n"
        f"Preço alvo: {preco_alvo:.2f} | Preço atual: {preco_atual:.2f}\n\n"
        "COMPLIANCE: AGUARDAR CANDLE 60 MIN."
    )
    remetente = "avisoscanal1milhao@gmail.com"
    senha_ou_token = st.secrets.get("gmail_app_password", "anoe gegm boqj ldzo")
    destinatario = "docs1milhao@gmail.com"
    assunto = f"ALERTA: {msg_op} em {ticker_symbol_sem_ext}"
    token_telegram = st.secrets.get("telegram_token", "6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
    chat_ids = [st.secrets.get("telegram_chat_id", "-1002533284493")]
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
        return int((hoje_abre - dt_now).total_seconds()), hoje_abre
    elif dt_now > hoje_fecha:
        amanha_abre = hoje_abre + datetime.timedelta(days=1)
        return int((amanha_abre - dt_now).total_seconds()), amanha_abre
    else:
        return 0, hoje_abre

def fmt_hms(seg):
    h = seg // 3600
    m = (seg % 3600) // 60
    s = seg % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ---------- LOG: cor por ticker + box rolável + ordem decrescente ----------
def ensure_color_map():
    if "ticker_colors" not in st.session_state:
        st.session_state.ticker_colors = {}

def color_for_ticker(ticker):
    ensure_color_map()
    if ticker not in st.session_state.ticker_colors:
        idx = len(st.session_state.ticker_colors) % len(PALETTE)
        st.session_state.ticker_colors[ticker] = PALETTE[idx]
    return st.session_state.ticker_colors[ticker]

# regex atualizado: aceita letras e números intercalados (ex: B3SA3.SA)
TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\.SA\b")   # ex: B3SA3.SA, PETR4.SA, ITUB4.SA
PLAIN_TICKER_PAT = re.compile(r"\b([A-Z0-9]{4,6})\b")  # ex: B3SA3, PETR4, VALE3

def extract_ticker(line):
    m = TICKER_PAT.search(line)
    if m:
        return m.group(1)
    m2 = PLAIN_TICKER_PAT.search(line)
    return m2.group(1) if m2 else None

def render_log_html(lines, selected_tickers=None, max_lines=200):
    """Renderiza o log com cores por ticker, box rolável e ordem decrescente (sem animação para evitar piscar)."""
    if not lines:
        st.write("—")
        return
    subset = lines[-max_lines:][::-1]  # mais novo no topo
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
# ESTADOS GLOBAIS (defaults)
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"]:
    if var not in st.session_state:
        st.session_state[var] = {} if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos", "ultimo_update_tempo"] else []

# Modo edição/pausa (COMEÇA ATIVO = False)
if "pausado" not in st.session_state:
    st.session_state.pausado = False
# Último estado de pausa (para evitar spam)
if "ultimo_estado_pausa" not in st.session_state:
    st.session_state.ultimo_estado_pausa = None
# Pontos de disparo (para marcar ⭐ no gráfico)
if "disparos" not in st.session_state:
    st.session_state.disparos = {}  # { 'TICKER': [(datetime, preco), ...] }

ensure_color_map()

# Carrega o estado salvo
carregar_estado()

# -----------------------------
# SIDEBAR - CONFIGURAÇÕES
# -----------------------------
st.sidebar.header("⚙️ Configurações")

# Botão de reset total da tabela/estado
if st.sidebar.button("🧹 Apagar estado salvo (reset total)"):
    try:
        if os.path.exists(SAVE_PATH):
            os.remove(SAVE_PATH)
        # zera tudo e deixa ATIVO por padrão
        st.session_state.clear()
        st.session_state.pausado = False
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
        # registra no novo log “limpo”
        now_tmp = datetime.datetime.now(TZ)
        st.session_state.log_monitoramento.append(
            f"{now_tmp.strftime('%H:%M:%S')} | 🧹 Reset manual do estado executado"
        )
        salvar_estado()
        st.sidebar.success("✅ Estado salvo apagado e reiniciado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro ao apagar estado: {e}")

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

# Filtro por ticker no LOG
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
        st.session_state.ultimo_update_tempo[ticker] = None
        st.success(f"Ativo {ticker} adicionado com sucesso!")
        salvar_estado()

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
        except Exception:
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
    tabela_status.dataframe(df, use_container_width=True, height=220)  # menos flicker
else:
    st.info("Nenhum ativo cadastrado ainda.")

st.subheader("📉 Gráfico em Tempo Real dos Preços")
grafico = st.empty()

st.subheader("🕒 Log de Monitoramento")
# Cartão único de contagem regressiva (fora do pregão)
countdown_container = st.empty()
# Log estilizado
log_container = st.empty()

# -----------------------------
# CICLO ÚNICO + REEXECUÇÃO AUTOMÁTICA
# -----------------------------
sleep_segundos = 60  # padrão fora do pregão / pausado

# evita spam de log quando alterna pausa
if st.session_state.pausado != st.session_state.ultimo_estado_pausa:
    st.session_state.ultimo_estado_pausa = st.session_state.pausado

if st.session_state.pausado:
    pass  # não monitora; mantém a página viva
else:
    if dentro_pregao(now):
        # ---- Notificação única na abertura do pregão ----
        if not st.session_state.get("avisou_abertura_pregao", False):
            st.session_state["avisou_abertura_pregao"] = True
            try:
                token = st.secrets.get("telegram_token", "").strip()
                chat = st.secrets.get("telegram_chat_id", "").strip()
                if not token or not chat:
                    raise ValueError("Token ou chat_id ausente em st.secrets")
                bot = Bot(token=token)
                asyncio.run(bot.send_message(chat_id=chat, text="🤖 Robô ativo — Pregão Aberto! 📈"))
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | 📣 Mensagem Telegram enviada: Pregão Aberto"
                )
            except Exception as e:
                st.session_state.log_monitoramento.append(
                    f"{now.strftime('%H:%M:%S')} | ⚠️ Erro ao enviar notificação de abertura: {e}"
                )

        # Esconde o cartão de countdown quando entra no pregão
        countdown_container.empty()

        # 1) Atualiza tabela/gráfico e monitora
        data = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            st.session_state.em_contagem.setdefault(t, False)
            st.session_state.status.setdefault(t, "🟢 Monitorando")
            st.session_state.ultimo_update_tempo.setdefault(t, None)

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
            tabela_status.dataframe(pd.DataFrame(data), use_container_width=True, height=220)

        # Lógica por ativo
        tickers_para_remover = []  # para tirar da busca após disparo
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
                (operacao_atv == "venda" and preco_atual <= preco_alvo)
            )

            if condicao:
                st.session_state.status[t] = "🟡 Em contagem"

                # Inicia ou continua a contagem de tempo
                if not st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = True
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.ultimo_update_tempo[t] = now
                    st.session_state.log_monitoramento.append(
                        f"{now.strftime('%H:%M:%S')} | {t}: Entrou em contagem (preço alvo atingido)"
                    )
                else:
                    # Atualiza o tempo acumulado
                    ultimo_update = st.session_state.ultimo_update_tempo.get(t)
                    if ultimo_update:
                        delta_t = (now - ultimo_update).total_seconds()
                        st.session_state.tempo_acumulado[t] += delta_t
                    st.session_state.ultimo_update_tempo[t] = now

                    # Verifica se atingiu o tempo máximo para disparo
                    if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                        st.session_state.status[t] = "🟥 Disparado"
                        st.session_state.em_contagem[t] = False
                        mensagem = notificar_preco_alvo_alcancado(tk_full, preco_alvo, preco_atual, operacao_atv)
                        st.session_state.historico_alertas.append({
                            "ticker": t,
                            "operacao": operacao_atv,
                            "preco_alvo": preco_alvo,
                            "preco_atual": preco_atual,
                            "hora": now.strftime("%Y-%m-%d %H:%M:%S")
                        })
                        st.session_state.disparos.setdefault(t, []).append((now, preco_atual))
                        st.session_state.log_monitoramento.append(
                            f"{now.strftime('%H:%M:%S')} | {t}: ⭐ Alerta disparado: {mensagem}"
                        )
                        tickers_para_remover.append(t)
            else:
                # Saiu da zona de preço alvo, reseta contagem
                if st.session_state.em_contagem.get(t, False):
                    st.session_state.em_contagem[t] = False
                    st.session_state.tempo_acumulado[t] = 0
                    st.session_state.status[t] = "🟢 Monitorando"
                    st.session_state.log_monitoramento.append(
                        f"{now.strftime('%H:%M:%S')} | {t}: Saiu da zona de preço alvo, contagem resetada"
                    )

        # Remove tickers que dispararam
        for t in tickers_para_remover:
            st.session_state.ativos = [a for a in st.session_state.ativos if a["ticker"] != t]
            st.session_state.log_monitoramento.append(
                f"{now.strftime('%H:%M:%S')} | {t}: Removido após disparo"
            )

        # Mantém o log limitado
        st.session_state.log_monitoramento = st.session_state.log_monitoramento[-LOG_MAX_LINHAS:]

        # Salva o estado após atualizações
        salvar_estado()

        # Define o tempo de espera para o próximo ciclo
        sleep_segundos = INTERVALO_VERIFICACAO
    else:
        # Fora do pregão: mostra contagem regressiva
        segundos, proxima_abertura = segundos_ate_abertura(now)
        countdown_container.markdown(
            f"⏳ Fora do pregão. Próxima abertura: {proxima_abertura.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            f"({fmt_hms(segundos)})"
        )

# Renderiza o log
render_log_html(st.session_state.log_monitoramento, selected_tickers)

# Mantém a página viva (reexecução automática)
if not st.session_state.pausado:
    time.sleep(sleep_segundos)
    st.rerun()