import streamlit as st
from yahooquery import Ticker
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
import asyncio
from telegram import Bot
import pandas as pd
import plotly.graph_objects as go

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
st.set_page_config(page_title="CLUBE - COMPRA E VENDA", layout="wide")

HORARIO_INICIO_PREGAO = datetime.time(14, 0, 0)
HORARIO_FIM_PREGAO = datetime.time(21, 0, 0)
INTERVALO_VERIFICACAO = 300   # 5 minutos
TEMPO_ACUMULADO_MAXIMO = 900  # <-- você configurou 900 (15 min) para teste; 25 min seria 1500

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
    # tenta preço em tempo real primeiro
    try:
        p = ticker_data.price[ticker_symbol].get("regularMarketPrice")
        if p is not None:
            return float(p)
    except Exception:
        pass
    # fallback último fechamento
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

def dentro_pregao(now: datetime.datetime) -> bool:
    t = now.time()
    return HORARIO_INICIO_PREGAO <= t <= HORARIO_FIM_PREGAO

def segundos_ate_proxima_abertura(now: datetime.datetime) -> int:
    hoje_abre = now.replace(hour=HORARIO_INICIO_PREGAO.hour, minute=HORARIO_INICIO_PREGAO.minute,
                            second=0, microsecond=0)
    fecha = now.replace(hour=HORARIO_FIM_PREGAO.hour, minute=HORARIO_FIM_PREGAO.minute,
                        second=0, microsecond=0)
    if now < hoje_abre:
        return int((hoje_abre - now).total_seconds())
    if now > fecha:
        amanha = hoje_abre + datetime.timedelta(days=1)
        return int((amanha - now).total_seconds())
    return 0  # já está no pregão

# -----------------------------
# ESTADOS GLOBAIS
# -----------------------------
for var in ["ativos", "historico_alertas", "log_monitoramento", "tempo_acumulado",
            "em_contagem", "status", "precos_historicos", "monitorando", "last_run"]:
    if var not in st.session_state:
        if var in ["tempo_acumulado", "em_contagem", "status", "precos_historicos"]:
            st.session_state[var] = {}
        elif var == "monitorando":
            st.session_state[var] = False
        elif var == "last_run":
            st.session_state[var] = None
        else:
            st.session_state[var] = []

# -----------------------------
# SIDEBAR - CONFIGURAÇÕES E TELEGRAM
# -----------------------------
st.sidebar.header("⚙️ Configurações")
token_telegram = st.sidebar.text_input("Token do Bot Telegram", type="password",
                                       value="6357672250:AAFfn3fIDi-3DS3a4DuuD09Lf-ERyoMgGSY")
chat_id_teste = st.sidebar.text_input("Chat ID (grupo ou usuário)", value="-1002533284493")

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
st.title("📈 CLUBE - COMPRA E VENDA")
st.write("Cadastre tickers, operações e preços alvo. Use **Iniciar** para começar o monitoramento reativo.")

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
# COMPONENTES DINÂMICOS
# -----------------------------
st.subheader("📊 Status dos Ativos Monitorados")
tabela_status = st.empty()
st.subheader("📉 Gráfico em Tempo Real dos Preços")
grafico = st.empty()
st.subheader("🕒 Log de Monitoramento")
log_box = st.empty()

# -----------------------------
# BOTÕES DE CONTROLE
# -----------------------------
colA, colB = st.columns(2)
if colA.button("🚀 Iniciar monitoramento"):
    st.session_state.monitorando = True
if colB.button("🛑 Parar monitoramento"):
    st.session_state.monitorando = False
    st.warning("⏹ Monitoramento interrompido manualmente.")

# -----------------------------
# 1) RENDERIZAÇÃO DA TABELA (SEMPRE)
# -----------------------------
def render_tabela_e_grafico():
    if st.session_state.ativos:
        now = datetime.datetime.now()
        data = []
        for ativo in st.session_state.ativos:
            t = ativo["ticker"]
            try:
                preco_atual = obter_preco_atual(f"{t}.SA")
            except Exception:
                preco_atual = "-"
            if preco_atual != "-":
                st.session_state.precos_historicos.setdefault(t, []).append((now, preco_atual))
            tempo = st.session_state.tempo_acumulado.get(t, 0)
            data.append({
                "Ticker": t,
                "Operação": ativo["operacao"].upper(),
                "Preço Alvo": f"R$ {ativo['preco']:.2f}",
                "Preço Atual": f"R$ {preco_atual}" if preco_atual != "-" else "-",
                "Status": st.session_state.status.get(t, "🟢 Monitorando"),
                "Tempo Acumulado": f"{int(tempo/60)} min"
            })
        df = pd.DataFrame(data)
        tabela_status.table(df)

        # gráfico colorido por status
        fig = go.Figure()
        for t, dados in st.session_state.precos_historicos.items():
            if len(dados) > 1:
                tempos, precos = zip(*dados)
                status = st.session_state.status.get(t, "🟢 Monitorando")
                cor = "green" if "🟢" in status else "orange" if "🟡" in status else "red"
                fig.add_trace(go.Scatter(x=tempos, y=precos, mode="lines+markers",
                                         name=t, line=dict(color=cor)))
        fig.update_layout(title="📉 Evolução dos Preços Monitorados",
                          xaxis_title="Tempo", yaxis_title="Preço (R$)",
                          legend_title="Ticker")
        grafico.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Nenhum ativo cadastrado ainda.")

render_tabela_e_grafico()

# -----------------------------
# 2) UM CICLO DE MONITORAMENTO (SEM WHILE/SLEEP)
# -----------------------------
def ciclo_monitoramento():
    now = datetime.datetime.now()
    last_run = st.session_state.last_run
    # tempo decorrido desde o último ciclo (para acumular com precisão)
    dt_secs = 0 if last_run is None else max(0, int((now - last_run).total_seconds()))
    st.session_state.last_run = now

    if not st.session_state.monitorando:
        return  # nada a fazer

    if not dentro_pregao(now):
        st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | ⏸ Fora do horário de pregão.")
        return

    for ativo in st.session_state.ativos:
        t = ativo["ticker"]
        preco_alvo = ativo["preco"]
        operacao = ativo["operacao"]
        ticker_symbol_full = f"{t}.SA"

        try:
            preco_atual = obter_preco_atual(ticker_symbol_full)
        except Exception as e:
            st.session_state.log_monitoramento.append(f"{now.strftime('%H:%M:%S')} | Erro ao buscar {t}: {e}")
            continue

        st.session_state.log_monitoramento.append(
            f"{now.strftime('%H:%M:%S')} | {ticker_symbol_full}: R$ {preco_atual:.2f}"
        )

        condicao_atingida = (
            (operacao == "compra" and preco_atual >= preco_alvo) or
            (operacao == "venda"  and preco_atual <= preco_alvo)
        )

        if condicao_atingida:
            st.session_state.status[t] = "🟡 Em contagem"
            if not st.session_state.em_contagem[t]:
                st.session_state.em_contagem[t] = True
                st.session_state.tempo_acumulado[t] = 0
                st.session_state.log_monitoramento.append(
                    f"⚠️ {t} atingiu o alvo ({preco_alvo:.2f}). Iniciando contagem..."
                )
            # acumula de acordo com o tempo decorrido desde a última execução (cap a INTERVALO_VERIFICACAO)
            incremento = INTERVALO_VERIFICACAO if dt_secs == 0 else min(dt_secs, INTERVALO_VERIFICACAO)
            st.session_state.tempo_acumulado[t] += incremento
            st.session_state.log_monitoramento.append(
                f"⏱ {t}: {st.session_state.tempo_acumulado[t]}s acumulados"
            )

            if st.session_state.tempo_acumulado[t] >= TEMPO_ACUMULADO_MAXIMO:
                alerta_msg = notificar_preco_alvo_alcancado(
                    ticker_symbol_full, preco_alvo, preco_atual, operacao, token_telegram
                )
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

# roda um ciclo (se necessário)
ciclo_monitoramento()

# mostra últimas linhas do log
if st.session_state.log_monitoramento:
    log_box.text("\n".join(st.session_state.log_monitoramento[-20:]))

# -----------------------------
# 3) AUTO-REFRESH INTELIGENTE (SEM BLOQUEAR)
# -----------------------------
now = datetime.datetime.now()
if st.session_state.monitorando:
    if dentro_pregao(now):
        prox_segundos = INTERVALO_VERIFICACAO               # em pregão: 5 min (ou o que você definiu)
    else:
        # fora do pregão: ping leve de keep-alive até abrir
        seg_ate_abertura = max(1, segundos_ate_proxima_abertura(now))
        prox_segundos = min(seg_ate_abertura, 300)          # ex.: a cada 5 min até abrir
else:
    # sem monitoramento ligado: atualiza raramente só pra manter sessão fresca
    prox_segundos = 600  # 10 min

st.caption(f"🔄 Próxima atualização automática em ~{prox_segundos} segundos.")
# Força recarregar o app no navegador (mantém sessão viva sem while/sleep)
st.markdown(
    f"<script>setTimeout(function(){{ window.location.reload(); }}, {prox_segundos*1000});</script>",
    unsafe_allow_html=True
)





