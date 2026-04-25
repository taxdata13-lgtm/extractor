"""
banks.py — Parsers modulares para cada layout bancário.

Cada parser retorna um ParseResult com:
  - lancamentos: lançamentos extraídos com sucesso
  - ignoradas:   linhas que pareciam transações mas não puderam ser processadas

Para adicionar um novo banco:
  1. Crie uma subclasse de BankParser.
  2. Defina BANK_SIGNATURES e implemente parse().
  3. Use _skip() para registrar toda linha pulada com um motivo.
  4. Registre no dicionário PARSERS.
"""

import re
import datetime
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Type

from models import Lancamento, LinhaIgnorada, ParseResult, parse_valor_br, is_debito, is_credito

log = logging.getLogger("conversor.banks")

# Padrão genérico para detectar linhas que SE PARECEM com transações mas
# não foram capturadas pelo parser principal — usado no pós-processamento.
_LINHA_SUSPEITA_RE = re.compile(
    r'\d{2}/\d{2}.*R\$\s*[\d.,]+[DC]'
)


# ── Classe base ───────────────────────────────────────────────────────────────

class BankParser(ABC):
    """Interface comum para todos os parsers."""

    BANK_SIGNATURES: List[str] = []

    def __init__(self) -> None:
        self._result: ParseResult = ParseResult()

    @abstractmethod
    def parse(self, full_text: str) -> ParseResult:
        """
        Processa o texto completo do PDF.

        Retorna ParseResult com lançamentos e relatório de linhas ignoradas.
        """

    @classmethod
    def matches(cls, text: str) -> bool:
        text_upper = text.upper()
        return any(sig.upper() in text_upper for sig in cls.BANK_SIGNATURES)

    # ── Helpers compartilhados ────────────────────────────────────────────────

    @staticmethod
    def _clean_description(desc: str) -> str:
        return re.sub(r'\s{2,}', ' ', desc).strip()

    def _skip(
        self,
        line_number: int,
        line: str,
        motivo: str,
        total_lines: int,
        pages: int,
    ) -> None:
        """Registra uma linha ignorada no resultado."""
        pagina_est = max(1, round((line_number / total_lines) * pages))
        self._result.ignoradas.append(LinhaIgnorada(
            numero_linha=line_number,
            conteudo=line.strip()[:120],
            motivo=motivo,
            pagina_estimada=pagina_est,
        ))

    def _varredura_pos_processamento(
        self,
        lines: List[str],
        linhas_capturadas: set,
        pages: int,
    ) -> None:
        """
        Varre todas as linhas em busca de padrões data+R$+D/C que NÃO foram
        capturados pelo parser, registrando-as como ignoradas com motivo
        'Padrão detectado mas não capturado'.
        """
        total = len(lines)
        for i, line in enumerate(lines, start=1):
            if i in linhas_capturadas:
                continue
            if _LINHA_SUSPEITA_RE.search(line):
                self._skip(i, line, "Padrão detectado mas não capturado pelo regex principal", total, pages)


# ── Sicoob — Internet Banking ─────────────────────────────────────────────────

class SicoobParser(BankParser):
    """
    Parser para extratos do Sicoob via Internet Banking (HTML → PDF).

    Layout (pdfplumber layout=True):
        [6 espaços] DD/MM  [doc]  [histórico]  R$ [valor][D|C]
    """

    BANK_SIGNATURES = [
        "SICOOB",
        "COOPERATIVAS DE CREDITO DO BRASIL",
        "COOPERATIVAS DE CREDITO",
        "SISBR",
        "SICOOBNET",
    ]

    # Verificador rápido: linha tem aparência de transação?
    _TX_RE = re.compile(
        r'^\s{4,8}\d{2}/\d{2}\s+'
        r'\S.*?\s{2,}'           # documento: qualquer coisa a partir de \S, lazy (aceita espaços internos)
        r'.+?\s{2,}'
        r'R\$\s*[\d.,]+[DC]\s*$'
    )

    # Captura os grupos de interesse
    _TX_CAPTURE = re.compile(
        r'^\s{4,8}(\d{2}/\d{2})\s+'
        r'\S.*?\s{2,}'           # documento (descartado): aceita "SICOOB SEG", "TOKIO SEGU" etc.
        r'(.+?)\s{2,}'
        r'R\$\s*([\d.,]+)([DC])\s*$'
    )

    _PERIOD_RE = re.compile(r'Periodo:.*?(\d{4})')

    _SKIP_HIST = frozenset([
        'SALDO ANTERIOR', 'SALDO BLOQUEADO ANTERIOR',
        'SALDO DO DIA', 'SALDO BLOQUEADO', 'RESUMO',
    ])

    _TRIVIAL_CONT = re.compile(
        r'^(Pagamento Pix|Recebimento Pix|Estorno Pix|Transferencia Pix|'
        r'Transferência Pix|https?://\S+|\d+/\d+)$',
        re.IGNORECASE,
    )

    _DOC_RE = re.compile(r'^[\d*]+[.\s][\d*]+[\s.-][\d*]{4}[-.]?[\d*]*$')

    def parse(self, full_text: str) -> ParseResult:
        self._result = ParseResult(banco="sicoob")
        year = self._extract_year(full_text)

        lines = full_text.split('\n')
        total = len(lines)
        # Estima o número de páginas contando cabeçalhos de página
        pages = max(1, full_text.count('Sicoob | Internet Banking'))

        self._result.total_linhas_pdf = total
        linhas_capturadas: set = set()

        i = 0
        while i < total:
            line = lines[i]
            line_num = i + 1  # 1-based

            if not self._TX_RE.match(line):
                i += 1
                continue

            # Linha tem aparência de transação — tenta captura completa
            m = self._TX_CAPTURE.match(line)
            if not m:
                self._skip(
                    line_num, line,
                    "Formato inesperado: linha parece transação mas regex de captura falhou",
                    total, pages,
                )
                i += 1
                continue

            date_dm, hist_raw, valor_str, nat = m.groups()
            hist = hist_raw.strip()
            linhas_capturadas.add(line_num)

            # Linha de saldo — ignorada intencionalmente (não é erro)
            if hist.upper() in self._SKIP_HIST:
                linhas_capturadas.add(line_num)  # evita falso positivo no pós-processamento
                i += 1
                continue

            # Coleta linhas de continuação
            detail_parts: List[str] = []
            j = i + 1
            while j < total:
                next_line = lines[j]
                if self._TX_RE.match(next_line):
                    break
                stripped = next_line.strip()
                indent = len(next_line) - len(next_line.lstrip(' '))
                if stripped and indent >= 20:
                    if not self._TRIVIAL_CONT.match(stripped) and not self._DOC_RE.match(stripped):
                        detail_parts.append(stripped)
                j += 1

            # Tenta converter valor
            valor = parse_valor_br(valor_str)
            if valor is None:
                self._skip(
                    line_num, line,
                    f"Valor inválido não pôde ser convertido: {valor_str!r}",
                    total, pages,
                )
                i = j
                continue

            # Valida natureza do lançamento
            if nat not in ('D', 'C'):
                self._skip(
                    line_num, line,
                    f"Indicador D/C desconhecido: {nat!r}",
                    total, pages,
                )
                i = j
                continue

            day, month = date_dm.split('/')
            data_full = f"{day}/{month}/{year}"
            desc = self._build_description(hist, detail_parts)

            self._result.lancamentos.append(Lancamento(
                data=data_full,
                descricao=self._clean_description(desc),
                debito=valor if nat == 'D' else None,
                credito=valor if nat == 'C' else None,
            ))

            i = j

        # Pós-processamento: procura transações que possam ter escapado do regex
        self._varredura_pos_processamento(lines, linhas_capturadas, pages)

        log.debug(
            f"Sicoob: {self._result.total_lancamentos} lançamentos, "
            f"{self._result.total_ignoradas} linhas ignoradas."
        )
        return self._result

    def _extract_year(self, text: str) -> str:
        m = self._PERIOD_RE.search(text)
        return m.group(1) if m else str(datetime.date.today().year)

    def _build_description(self, hist: str, details: List[str]) -> str:
        parts = [hist]
        fav = next((d for d in details if d.upper().startswith('FAV.:')), None)
        notes = [d for d in details if not d.upper().startswith('FAV.:')]
        if fav:
            parts.append(fav)
        if notes:
            parts.append(' | '.join(notes[:2]))
        return ' - '.join(p for p in parts if p)


# ── Santander ─────────────────────────────────────────────────────────────────

class SantanderParser(BankParser):
    """
    Parser para extratos do Banco Santander.

    Layout: DD/MM/AAAA  DESCRIÇÃO  1.234,56 D
    """

    BANK_SIGNATURES = ["SANTANDER", "BANCO SANTANDER"]

    _LINE_RE = re.compile(
        r'(\d{2}/\d{2}/\d{2,4})\s+(.+?)\s+([\d.,]+)\s*([DCdc])\b',
        re.MULTILINE,
    )

    # Detecta linhas com data + valor mas sem indicador D/C — podem ser erros
    _SUSPEITA_RE = re.compile(
        r'(\d{2}/\d{2}/\d{2,4})\s+.+?\s+([\d.,]+)\s*$',
        re.MULTILINE,
    )

    def parse(self, full_text: str) -> ParseResult:
        self._result = ParseResult(banco="santander")
        lines = full_text.split('\n')
        self._result.total_linhas_pdf = len(lines)
        pages = max(1, full_text.count('\f') + 1)

        capturadas_spans = set()

        for m in self._LINE_RE.finditer(full_text):
            capturadas_spans.add(m.start())
            data = self._normalize_date(m.group(1))
            desc = self._clean_description(m.group(2))
            valor = parse_valor_br(m.group(3))
            nat = m.group(4)

            if valor is None:
                # Localiza a linha para o relatório
                linha_num = full_text[:m.start()].count('\n') + 1
                self._skip(
                    linha_num, m.group(0),
                    f"Valor inválido: {m.group(3)!r}",
                    len(lines), pages,
                )
                continue

            self._result.lancamentos.append(Lancamento(
                data=data, descricao=desc,
                debito=valor if is_debito(nat) else None,
                credito=valor if is_credito(nat) else None,
            ))

        # Busca padrões suspeitos não capturados
        for m in self._SUSPEITA_RE.finditer(full_text):
            if m.start() not in capturadas_spans:
                linha_num = full_text[:m.start()].count('\n') + 1
                self._skip(
                    linha_num, m.group(0).strip(),
                    "Padrão detectado mas não capturado (sem indicador D/C?)",
                    len(lines), pages,
                )

        log.debug(f"Santander: {self._result.total_lancamentos} lançamentos, {self._result.total_ignoradas} ignoradas.")
        return self._result

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        p = date_str.split('/')
        if len(p[2]) == 2:
            p[2] = '20' + p[2]
        return '/'.join(p)


# ── Banco do Brasil ───────────────────────────────────────────────────────────

class BancoBrasilParser(BankParser):
    """
    Parser para extratos do Banco do Brasil.

    Layout: DD/MM/AAAA  DESCRIÇÃO  -1.234,56  (negativo = débito)
    """

    BANK_SIGNATURES = ["BANCO DO BRASIL", "BB S.A.", "BANCO DO BRASIL S.A"]

    _LINE_RE = re.compile(
        r'(\d{2}/\d{2}/\d{2,4})\s+(.+?)\s+(-?[\d.,]+)\s*$',
        re.MULTILINE,
    )

    def parse(self, full_text: str) -> ParseResult:
        self._result = ParseResult(banco="bb")
        lines = full_text.split('\n')
        self._result.total_linhas_pdf = len(lines)
        pages = max(1, full_text.count('\f') + 1)

        for m in self._LINE_RE.finditer(full_text):
            data = self._normalize_date(m.group(1))
            desc = self._clean_description(m.group(2))
            raw_valor = m.group(3).strip()
            negativo = raw_valor.startswith('-')
            valor = parse_valor_br(raw_valor.lstrip('-'))

            if valor is None:
                linha_num = full_text[:m.start()].count('\n') + 1
                self._skip(
                    linha_num, m.group(0).strip(),
                    f"Valor inválido: {raw_valor!r}",
                    len(lines), pages,
                )
                continue

            self._result.lancamentos.append(Lancamento(
                data=data, descricao=desc,
                debito=valor if negativo else None,
                credito=valor if not negativo else None,
            ))

        log.debug(f"BB: {self._result.total_lancamentos} lançamentos, {self._result.total_ignoradas} ignoradas.")
        return self._result

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        p = date_str.split('/')
        if len(p[2]) == 2:
            p[2] = '20' + p[2]
        return '/'.join(p)


# ── Registro global ───────────────────────────────────────────────────────────

PARSERS: Dict[str, Type[BankParser]] = {
    "santander": SantanderParser,
    "bb":        BancoBrasilParser,
    "sicoob":    SicoobParser,
}


def detect_bank(full_text: str) -> Optional[str]:
    """Detecta automaticamente o banco pelas assinaturas no texto do PDF."""
    for key, cls in PARSERS.items():
        if cls.matches(full_text):
            return key
    return None
