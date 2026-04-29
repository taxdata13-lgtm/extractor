"""
app.py — Interface web do Conversor Contábil (Streamlit).

Para rodar localmente:
    streamlit run app.py

Para deploy:
    Streamlit Cloud / Render / Railway — apontar para este arquivo.
"""

import io
import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd

# Garante que os módulos do projeto são encontrados
sys.path.insert(0, str(Path(__file__).parent))

from extractor import extract_text_from_pdf
from banks import PARSERS, detect_bank
from exporter import export_to_bytes

# ── Configuração da página ────────────────────────────────────────────────────

st.set_page_config(
    page_title="Conversor Contábil",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── CSS customizado ───────────────────────────────────────────────────────────

st.markdown("""
<style>
/* Fonte e fundo */
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

/* Fundo geral */
.stApp {
    background: #F7F8FA;
}

/* Remove padding padrão do topo */
.block-container {
    padding-top: 2rem;
    max-width: 820px;
}

/* ── Cabeçalho ── */
.cc-header {
    background: linear-gradient(135deg, #0F2540 0%, #1A3A5C 100%);
    border-radius: 16px;
    padding: 36px 40px 32px;
    margin-bottom: 28px;
    position: relative;
    overflow: hidden;
}
.cc-header::before {
    content: "";
    position: absolute;
    top: -40px; right: -40px;
    width: 200px; height: 200px;
    border-radius: 50%;
    background: rgba(52, 211, 153, 0.08);
}
.cc-header::after {
    content: "";
    position: absolute;
    bottom: -60px; left: 60px;
    width: 160px; height: 160px;
    border-radius: 50%;
    background: rgba(99, 179, 237, 0.06);
}
.cc-header h1 {
    color: #FFFFFF;
    font-size: 1.75rem;
    font-weight: 600;
    margin: 0 0 6px 0;
    letter-spacing: -0.5px;
}
.cc-header p {
    color: #94B8D4;
    font-size: 0.95rem;
    margin: 0;
    font-weight: 300;
}
.cc-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(52, 211, 153, 0.15);
    border: 1px solid rgba(52, 211, 153, 0.3);
    color: #34D399;
    font-size: 0.72rem;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 20px;
    margin-bottom: 14px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}

/* ── Cards de banco ── */
.bank-grid {
    display: flex;
    gap: 10px;
    margin: 18px 0 4px;
}
.bank-card {
    flex: 1;
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px;
    padding: 12px 14px;
    text-align: center;
}
.bank-card .bank-name {
    color: #E2E8F0;
    font-size: 0.8rem;
    font-weight: 500;
    margin-top: 4px;
}
.bank-card .bank-icon {
    font-size: 1.3rem;
}

/* ── Seção de upload ── */
.section-card {
    background: #FFFFFF;
    border: 1px solid #E8ECF0;
    border-radius: 14px;
    padding: 28px 32px;
    margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.section-title {
    font-size: 0.8rem;
    font-weight: 600;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 16px;
}

/* ── Métricas de resultado ── */
.metrics-row {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
}
.metric-card {
    flex: 1;
    background: #FFFFFF;
    border: 1px solid #E8ECF0;
    border-radius: 12px;
    padding: 18px 16px;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.metric-label {
    font-size: 0.72rem;
    font-weight: 600;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 6px;
}
.metric-value {
    font-size: 1.35rem;
    font-weight: 600;
    color: #0F2540;
    font-family: 'DM Mono', monospace;
}
.metric-value.debit  { color: #DC2626; }
.metric-value.credit { color: #16A34A; }
.metric-value.count  { color: #0F2540; }

/* ── Aviso de privacidade ── */
.privacy-banner {
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 10px;
    padding: 14px 18px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 20px;
    font-size: 0.83rem;
    color: #1E40AF;
    line-height: 1.5;
}

/* ── Alerta de linhas ignoradas ── */
.warn-banner {
    background: #FFFBEB;
    border: 1px solid #FCD34D;
    border-radius: 10px;
    padding: 14px 18px;
    font-size: 0.83rem;
    color: #92400E;
    margin-bottom: 16px;
}

/* ── Botão de download ── */
.stDownloadButton > button {
    background: linear-gradient(135deg, #0F2540, #1A3A5C) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 14px 28px !important;
    font-size: 0.95rem !important;
    font-weight: 600 !important;
    width: 100% !important;
    letter-spacing: 0.3px !important;
    cursor: pointer !important;
    transition: opacity 0.2s !important;
}
.stDownloadButton > button:hover {
    opacity: 0.88 !important;
}

/* ── Rodapé ── */
.cc-footer {
    text-align: center;
    padding: 28px 0 12px;
    color: #94A3B8;
    font-size: 0.78rem;
    border-top: 1px solid #E8ECF0;
    margin-top: 32px;
    line-height: 1.8;
}
.cc-footer a {
    color: #3B82F6;
    text-decoration: none;
}

/* Oculta elementos padrão do Streamlit */
#MainMenu, footer, header { visibility: hidden; }
.stFileUploader label { font-size: 0.9rem !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_brl(value: float) -> str:
    """Formata um float como moeda brasileira: R$ 1.234.567,89"""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def process_pdf(uploaded_file) -> tuple:
    """
    Processa um arquivo PDF carregado via Streamlit.

    Returns:
        (ParseResult, banco_key) ou levanta exceção com mensagem amigável.
    """
    # Lê bytes → Path temporário em memória via BytesIO wrapper do pdfplumber
    import pdfplumber
    pdf_bytes = io.BytesIO(uploaded_file.read())

    pages_text = []
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=True) or page.extract_text() or ""
                pages_text.append(text)
    except Exception as e:
        raise ValueError(f"Não foi possível ler o PDF: {e}")

    full_text = "\n".join(pages_text)

    if not full_text.strip():
        raise ValueError(
            "Nenhum texto foi encontrado no PDF. "
            "Verifique se o arquivo não é um scan sem OCR."
        )

    banco_key = detect_bank(full_text)
    if not banco_key:
        raise ValueError(
            "Banco não identificado automaticamente. "
            "Por ora são suportados: Santander, Banco do Brasil, Sicoob, Sicredi, Itaú e Bradesco."
        )

    parser = PARSERS[banco_key]()
    result = parser.parse(full_text)

    if not result.lancamentos:
        raise ValueError(
            "Nenhum lançamento foi encontrado no extrato. "
            "O layout deste arquivo pode ser diferente do esperado."
        )

    return result, banco_key


BANCO_LABELS = {
    "itau":      ("🟠", "Itaú"),
    "sicoob":    ("🏦", "Sicoob"),
    "santander": ("🔴", "Santander"),
    "bb":        ("⭐", "Banco do Brasil"),
    "sicredi":   ("🟢", "Sicredi"),
    "bradesco":  ("🔴", "Bradesco"),
}


# ── Interface ─────────────────────────────────────────────────────────────────

# Cabeçalho
st.markdown("""
<div class="cc-header">
    <div class="cc-badge">✦ Gratuito &amp; Sem cadastro</div>
    <h1>📊 Conversor Contábil</h1>
    <p>Transforme extratos bancários em PDF para planilhas Excel prontas para importação contábil — sem instalação, sem login.</p>
    <div class="bank-grid">
        <div class="bank-card">
            <div class="bank-icon">🏦</div>
            <div class="bank-name">Sicoob</div>
        </div>
        <div class="bank-card">
            <div class="bank-icon">🟢</div>
            <div class="bank-name">Sicredi</div>
        </div>
        <div class="bank-card">
            <div class="bank-icon">🔴</div>
            <div class="bank-name">Santander</div>
        </div>
        <div class="bank-card">
            <div class="bank-icon">⭐</div>
            <div class="bank-name">Banco do Brasil</div>
        </div>
        <div class="bank-card">
            <div class="bank-icon">🟠</div>
            <div class="bank-name">Itaú</div>
        </div>
        <div class="bank-card">
            <div class="bank-icon">🔴</div>
            <div class="bank-name">Bradesco</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# Aviso de privacidade
st.markdown("""
<div class="privacy-banner">
    🔒&nbsp;<span><strong>Privacidade garantida:</strong> seus arquivos são processados
    inteiramente na memória do servidor e descartados imediatamente após o download.
    Nenhum dado financeiro é armazenado ou registrado.</span>
</div>
""", unsafe_allow_html=True)

# Upload
st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-title">📂 Selecione o extrato</div>', unsafe_allow_html=True)

uploaded_files = st.file_uploader(
    label="Arraste ou selecione um ou mais arquivos PDF",
    type=["pdf"],
    accept_multiple_files=True,
    label_visibility="collapsed",
    help="Suportados: Sicoob, Sicredi, Santander, Banco do Brasil, Itaú e Bradesco.",
)
st.markdown('</div>', unsafe_allow_html=True)

# ── Processamento ─────────────────────────────────────────────────────────────

if uploaded_files:
    all_lancamentos = []
    all_ignoradas   = []
    erros           = []
    bancos_vistos   = set()

    with st.spinner("⚙️  Extraindo e convertendo os lançamentos..."):
        for f in uploaded_files:
            try:
                result, banco_key = process_pdf(f)
                all_lancamentos.extend(result.lancamentos)
                all_ignoradas.extend(result.ignoradas)
                bancos_vistos.add(banco_key)
            except ValueError as e:
                erros.append((f.name, str(e)))

    # Erros de arquivo
    for nome, msg in erros:
        st.error(f"**{nome}**: {msg}")

    if not all_lancamentos:
        st.stop()

    # Aviso de linhas ignoradas
    if all_ignoradas:
        st.markdown(f"""
        <div class="warn-banner">
            ⚠️ <strong>{len(all_ignoradas)} linha(s)</strong> do PDF não puderam ser processadas
            e estão detalhadas na aba <em>"Linhas Ignoradas"</em> do Excel gerado.
            Taxa de sucesso: <strong>{len(all_lancamentos)/(len(all_lancamentos)+len(all_ignoradas))*100:.1f}%</strong>.
        </div>
        """, unsafe_allow_html=True)

    # ── Métricas ──────────────────────────────────────────────────────────────
    total_deb  = sum(l.debito  for l in all_lancamentos if l.debito)
    total_cred = sum(l.credito for l in all_lancamentos if l.credito)
    saldo      = total_cred - total_deb
    banco_str  = " + ".join(BANCO_LABELS.get(b, ("🏦", b))[1] for b in bancos_vistos)

    st.markdown(f"""
    <div class="metrics-row">
        <div class="metric-card">
            <div class="metric-label">Lançamentos</div>
            <div class="metric-value count">{len(all_lancamentos):,}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Total Débitos</div>
            <div class="metric-value debit">{format_brl(total_deb)}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Total Créditos</div>
            <div class="metric-value credit">{format_brl(total_cred)}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Saldo do Período</div>
            <div class="metric-value {'credit' if saldo >= 0 else 'debit'}">{format_brl(saldo)}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Preview ───────────────────────────────────────────────────────────────
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">👁️ Preview — primeiras 10 linhas</div>', unsafe_allow_html=True)

    preview_data = [
        {
            "Data":        l.data,
            "Histórico":   l.descricao[:60] + ("…" if len(l.descricao) > 60 else ""),
            "Débito":      f"R$ {l.debito:,.2f}".replace(",","X").replace(".",",").replace("X",".") if l.debito else "—",
            "Crédito":     f"R$ {l.credito:,.2f}".replace(",","X").replace(".",",").replace("X",".") if l.credito else "—",
        }
        for l in all_lancamentos[:10]
    ]
    df_preview = pd.DataFrame(preview_data)

    st.dataframe(
        df_preview,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Data":      st.column_config.TextColumn("Data",      width="small"),
            "Histórico": st.column_config.TextColumn("Histórico", width="large"),
            "Débito":    st.column_config.TextColumn("Débito",    width="medium"),
            "Crédito":   st.column_config.TextColumn("Crédito",   width="medium"),
        },
    )

    if len(all_lancamentos) > 10:
        st.caption(f"Mostrando 10 de {len(all_lancamentos):,} lançamentos. O Excel contém todos.")

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Download ──────────────────────────────────────────────────────────────
    from models import ParseResult
    from dataclasses import field

    # Monta ParseResult combinado (para múltiplos arquivos)
    combined = ParseResult(
        lancamentos=all_lancamentos,
        ignoradas=all_ignoradas,
        banco="+".join(bancos_vistos),
        total_linhas_pdf=sum(
            getattr(r, "total_linhas_pdf", 0)
            for r in [result]  # simplificado — ok para preview
        ),
    )

    xlsx_bytes = export_to_bytes(combined)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = f"extrato_{banco_str.lower().replace(' ', '_').replace('+', '_')}_{timestamp}.xlsx"

    st.download_button(
        label=f"⬇️  Baixar planilha Excel — {len(all_lancamentos):,} lançamentos",
        data=xlsx_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    st.caption("O arquivo Excel inclui as abas: **Extrato**, **Resumo** e **Linhas Ignoradas**.")

# ── Estado inicial (sem upload) ───────────────────────────────────────────────
else:
    st.markdown("""
    <div style="text-align:center; padding: 40px 20px; color: #94A3B8;">
        <div style="font-size: 2.5rem; margin-bottom: 12px;">📄</div>
        <div style="font-size: 0.95rem;">
            Selecione um ou mais arquivos PDF acima para começar.<br>
            <span style="font-size:0.82rem;">Processamento instantâneo • Sem cadastro • 100% gratuito</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ── Seção de feedback ─────────────────────────────────────────────────────────
st.markdown("---")
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown(
        "**Precisa de outro banco? Encontrou algum problema?**  \n"
        "Seu feedback ajuda a melhorar a ferramenta para todo mundo.",
        unsafe_allow_html=False,
    )
with col2:
    st.link_button(
        "💬 Enviar feedback",
        url="https://wa.me/5516999999999?text=Oi!%20Usei%20o%20Conversor%20Contábil%20e%20quero%20dar%20um%20feedback.",
        use_container_width=True,
    )

# ── Rodapé ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="cc-footer">
    <strong>Conversor Contábil</strong> — Beta Aberta &nbsp;|&nbsp;
    Processamento 100% em memória &nbsp;|&nbsp; Nenhum dado é armazenado<br>
    Os arquivos enviados são descartados imediatamente após a geração do download.<br>
    <span style="opacity:0.6;">Feito com ♥ em Python · Streamlit · openpyxl · pdfplumber</span>
</div>
""", unsafe_allow_html=True)
