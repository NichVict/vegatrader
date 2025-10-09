import streamlit as st
st.set_page_config(page_title="Canal 1 Milhão - Robô de Operações", layout="wide")

st.title("🤖 Canal 1 Milhão - Monitor de Operações")
st.markdown("""
Bem-vindo ao **Painel Principal**.

No menu lateral, você pode escolher entre:

- **1 CARTEIRA CURTO PRAZO**
- **2 LOSS CURTO PRAZO**
- **3 CARTEIRA CURTÍSSIMO PRAZO**
- **4 LOSS CURTÍSSIMO PRAZO**
- **5 CLUBE**
- **6 LOSS CLUBE**

Cada página monitora preços, tempos acumulados e envia alertas automáticos.
""")
# (opcional) atalhos clicáveis para as páginas:
st.subheader("Acessos rápidos")

st.page_link("pages/curto.py", label="⚡ Curto Prazo")
st.page_link("pages/loss_curto.py", label="🚨 Loss Curto")
st.page_link("pages/curtissimo.py", label="⚡ Curtíssimo Prazo")
st.page_link("pages/loss_curtissimo.py", label="🚨 Loss Curtíssimo")
st.page_link("pages/clube.py", label="⚡ Clube")
st.page_link("pages/loss_clube.py", label="🚨 Loss Clube")




