"""
models.py — Tipos de dados e utilitários de parsing numérico no padrão BR.
"""

from dataclasses import dataclass, field
from typing import Optional, List
import re


@dataclass
class Lancamento:
    """Representa um único lançamento contábil extraído do extrato."""

    data: str                        # DD/MM/AAAA
    descricao: str                   # Histórico completo
    debito: Optional[float] = None   # Valor positivo ou None
    credito: Optional[float] = None  # Valor positivo ou None

    def __post_init__(self) -> None:
        # Garante que nunca haja sinal negativo em débito/crédito
        if self.debito is not None:
            self.debito = abs(self.debito)
        if self.credito is not None:
            self.credito = abs(self.credito)


@dataclass
class LinhaIgnorada:
    """Registra uma linha do PDF que foi detectada como transação mas não pôde ser processada."""

    numero_linha: int        # Número da linha no texto completo (1-based)
    conteudo: str            # Conteúdo bruto da linha (truncado a 120 chars)
    motivo: str              # Descrição do motivo pelo qual foi ignorada
    pagina_estimada: int = 0 # Página aproximada do PDF (estimada pela posição)

    def conteudo_resumido(self, max_len: int = 80) -> str:
        c = self.conteudo.strip()
        return c[:max_len] + "…" if len(c) > max_len else c


@dataclass
class ParseResult:
    """Resultado completo de um parse: lançamentos extraídos + relatório de erros."""

    lancamentos: List[Lancamento] = field(default_factory=list)
    ignoradas: List[LinhaIgnorada] = field(default_factory=list)
    total_linhas_pdf: int = 0
    banco: str = ""

    # ── Estatísticas derivadas ────────────────────────────────────────────────

    @property
    def total_lancamentos(self) -> int:
        return len(self.lancamentos)

    @property
    def total_ignoradas(self) -> int:
        return len(self.ignoradas)

    @property
    def taxa_sucesso(self) -> float:
        total = self.total_lancamentos + self.total_ignoradas
        return (self.total_lancamentos / total * 100) if total else 100.0

    @property
    def total_debitos(self) -> float:
        return sum(l.debito for l in self.lancamentos if l.debito)

    @property
    def total_creditos(self) -> float:
        return sum(l.credito for l in self.lancamentos if l.credito)

    def motivos_agrupados(self) -> dict:
        """Retorna contagem de linhas ignoradas agrupadas por motivo."""
        contagem: dict = {}
        for ign in self.ignoradas:
            contagem[ign.motivo] = contagem.get(ign.motivo, 0) + 1
        return dict(sorted(contagem.items(), key=lambda x: -x[1]))


# ── Helpers de parsing numérico ───────────────────────────────────────────────

_BR_NUMBER_RE = re.compile(r"[\d.,]+")


def parse_valor_br(raw: str) -> Optional[float]:
    """
    Converte um número no padrão brasileiro (1.234,56 ou 1234,56 ou 1,234.56)
    para float.

    Retorna None se o valor não puder ser interpretado.
    """
    raw = raw.strip().replace(" ", "")
    if not raw:
        return None

    # Remove indicadores de débito/crédito que alguns bancos adicionam
    raw = re.sub(r"[DCdc]$", "", raw).strip()

    # Detecta o padrão: ponto como separador de milhar e vírgula como decimal (BR)
    if re.search(r"\d\.\d{3},\d", raw):  # ex: 1.234,56
        raw = raw.replace(".", "").replace(",", ".")
    elif re.search(r"\d,\d{3}\.\d", raw):  # ex: 1,234.56 (padrão US)
        raw = raw.replace(",", "")
    elif "," in raw and "." not in raw:    # ex: 1234,56
        raw = raw.replace(",", ".")
    elif "." in raw and "," not in raw:    # ex: 1234.56 (já float) ou 1.234 (milhar sem decimal)
        # Ambíguo: se a parte após o ponto tiver != 3 dígitos, é decimal
        parts = raw.split(".")
        if len(parts[-1]) != 3:
            pass  # Já está no formato float correto
        else:
            raw = raw.replace(".", "")  # Milhar sem decimal → inteiro

    try:
        return float(raw)
    except ValueError:
        return None


def is_debito(indicador: str) -> bool:
    """Retorna True se o indicador de natureza apontar para débito."""
    return indicador.strip().upper() in {"D", "DB", "DEB", "-", "DÉBITO", "DEBITO"}


def is_credito(indicador: str) -> bool:
    """Retorna True se o indicador de natureza apontar para crédito."""
    return indicador.strip().upper() in {"C", "CR", "CRED", "+", "CRÉDITO", "CREDITO"}
