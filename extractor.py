"""
extractor.py — Extração de texto de PDFs via pdfplumber.

Todo o processamento ocorre em memória; nenhum arquivo temporário é gerado.
"""

from pathlib import Path
from typing import List

import pdfplumber


def extract_text_from_pdf(pdf_path: Path) -> List[str]:
    """
    Extrai o texto de cada página do PDF e retorna uma lista de strings.

    Args:
        pdf_path: Caminho para o arquivo PDF.

    Returns:
        Lista onde cada elemento é o texto de uma página.

    Raises:
        RuntimeError: Se a leitura do PDF falhar.
    """
    pages_text: List[str] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Tenta extração com layout preservado; fallback para texto simples.
                text = page.extract_text(layout=True) or page.extract_text() or ""
                pages_text.append(text)
    except Exception as exc:
        raise RuntimeError(f"Falha ao ler o PDF '{pdf_path}': {exc}") from exc

    return pages_text
