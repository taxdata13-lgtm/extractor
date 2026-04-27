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
    Parser para extratos do Banco do Brasil (Internet Banking / Consultas).

    Layout real do PDF (pdfplumber layout=True):
        DD/MM/AAAA   0000   LOTE   COD   Histórico [complemento]   DOC   VALOR [D|C]   SALDO [D|C]

    Estratégia linha-a-linha:
      1. Filtra linhas que começam com DD/MM/AAAA (data de balancete).
      2. Extrai o ÚLTIMO token numérico+D/C antes do saldo, que é o VALOR da
         transação.  O saldo (coluna final) é descartado.
      3. Tudo entre a data e o valor é tratado como descrição (após limpeza).
      4. Linhas de continuação (sem data) que contêm a Razão Social / CNPJ do
         beneficiário são coletadas e anexadas à descrição do lançamento anterior.
      5. Linhas de saldo (Saldo Anterior, BB Rende Fácil, S A L D O final) são
         ignoradas intencionalmente — não são erros.

    Identificação de débito/crédito:
      O BB usa sufixo 'D' (débito) ou 'C' (crédito) após cada valor e após o
      saldo.  Capturamos o indicador D/C do VALOR (primeira ocorrência antes do
      saldo).
    """

    BANK_SIGNATURES = [
        "BANCO DO BRASIL",
        "BB S.A.",
        "BANCO DO BRASIL S.A",
        "Consultas - Extrato de conta corrente",  # título do PDF web
        "G3310410",                               # código de geração BB
    ]

    # Linha âncora: indentação variável + data DD/MM/AAAA + resto
    _ANCHOR_DATE_RE = re.compile(r'^\s+(\d{2}/\d{2}/\d{2,4})\s+(.*)')

    # Valor monetário real com indicador D/C no final da linha.
    # Padrão BR: opcional sinal + grupos de até 3 dígitos separados por ponto + vírgula + 2 decimais
    # Ex: "57,82 C"  "13.786,09 C"  "130.787,23 C"  "141.818,25 D"
    # NÃO deve casar com: "340.516.634.007.704" (número de documento)
    _VALOR_DC_RE = re.compile(
        r'(-?(?:\d{1,3})(?:\.\d{3})*,\d{2})\s+([DC])'  # valor monetário BR + D/C
        r'(?:\s+(?:\d{1,3})(?:\.\d{3})*,\d{2}\s+[DC])?' # saldo opcional (descartado)
        r'\s*$'
    )

    # Linhas de saldo/cabeçalho que devem ser ignoradas silenciosamente
    _SKIP_RE = re.compile(
        r'Saldo\s+Anterior|BB\s+Rende\s+F[áa]cil|Rende\s+Facil'
        r'|S\s*A\s*L\s*D\s*O\b'
        r'|Dt\.\s*balancete|Dt\.\s*movimento'
        r'|Lan[çc]amentos'
        r'|Ag[êe]ncia\s+\d'
        r'|Conta\s+corrente'
        r'|Per[íi]odo\s+do\s+extrato'
        r'|Cliente\s*-|Consultas\s*-'
        r'|Servi[çc]o\s+de\s+Atendimento'
        r'|Transa[çc][ãa]o\s+efetuada'
        r'|Ouvidoria'
        r'|Hist[óo]rico\s+Documento'
        r'|Valor\s+R\$',
        re.IGNORECASE,
    )

    # Detecta se a linha de continuação contém apenas dígitos/separadores (ruído)
    _ONLY_DIGITS_RE = re.compile(r'^[\d\s./\-]+$')

    def parse(self, full_text: str) -> ParseResult:
        self._result = ParseResult(banco="bb")
        lines = full_text.split('\n')
        total = len(lines)
        self._result.total_linhas_pdf = total

        # Estima páginas pelo número de cabeçalhos de data no topo ou form feeds
        pages = max(1, full_text.count('\f') + full_text.count('G3310'))

        # ── Debug: exibe as primeiras 10 linhas brutas para diagnóstico ────────
        log.debug("=== BB RAW (primeiras 10 linhas) ===")
        for i, l in enumerate(lines[:10], 1):
            log.debug(f"  [{i:>3}] {l!r}")
        log.debug("=====================================")

        pending_lancamento: Optional[Lancamento] = None
        pending_line_num: int = 0

        def _flush_pending() -> None:
            """Confirma o lançamento pendente no resultado."""
            if pending_lancamento is not None:
                self._result.lancamentos.append(pending_lancamento)

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip()

            # ── Ignora linhas vazias ──────────────────────────────────────────
            if not line.strip():
                continue

            # ── Ignora cabeçalhos / rodapés / linhas de saldo ─────────────────
            if self._SKIP_RE.search(line):
                _flush_pending()
                pending_lancamento = None
                continue

            # ── Linha âncora: começa com data DD/MM/AAAA ──────────────────────
            m_anchor = self._ANCHOR_DATE_RE.match(line)
            if m_anchor:
                # Antes de processar nova âncora, confirma a pendente anterior
                _flush_pending()
                pending_lancamento = None

                date_str = self._normalize_date(m_anchor.group(1))
                rest = m_anchor.group(2).strip()

                # Extrai valor + indicador D/C do final da linha
                m_val = self._VALOR_DC_RE.search(rest)
                if not m_val:
                    # Linha começa com data mas não tem valor D/C reconhecível
                    # Pode ser linha de cabeçalho ou layout desconhecido
                    self._skip(
                        line_num, line,
                        "Linha com data mas sem valor D/C detectável",
                        total, pages,
                    )
                    continue

                raw_valor = m_val.group(1)   # ex: "13.786,09" ou "-80.000,00"
                nat = m_val.group(2)          # "D" ou "C"

                # Extrai descrição: tudo antes do par valor+D/C
                desc_raw = rest[:m_val.start()].strip()

                # Remove colunas numéricas prefixais do BB:
                #   "0000  14397821  Pix-Recebido..."  →  "Pix-Recebido..."
                # Formato: agência (4 dig) + espaços + lote+cod (7-8 dig) + espaço
                desc_raw = re.sub(r'^\d{4}\s+\d{5,8}\s+', '', desc_raw).strip()

                # Remove o número do documento do final (token numérico longo
                # com pontos, ex: "10.818.552.213.311" ou "100.146.637")
                # Esses aparecem APÓS o histórico e ANTES do valor
                desc_raw = re.sub(r'\s+[\d.]{7,}\s*$', '', desc_raw).strip()

                # Normaliza valor
                negativo = raw_valor.startswith('-')
                valor = parse_valor_br(raw_valor.lstrip('-'))

                if valor is None:
                    self._skip(
                        line_num, line,
                        f"Valor inválido não pôde ser convertido: {raw_valor!r}",
                        total, pages,
                    )
                    continue

                # Cria lançamento pendente (pode receber continuação)
                pending_lancamento = Lancamento(
                    data=date_str,
                    descricao=self._clean_description(desc_raw) or "(sem descrição)",
                    debito=valor  if (negativo or nat == 'D') else None,
                    credito=valor if (not negativo and nat == 'C') else None,
                )
                pending_line_num = line_num
                continue

            # ── Linha de continuação: enriquece a descrição do lançamento ─────
            if pending_lancamento is not None:
                stripped = line.strip()
                # Descarta linhas que são só dígitos/códigos numéricos
                if stripped and not self._ONLY_DIGITS_RE.match(stripped):
                    # Anexa à descrição apenas se não for ruído de layout
                    # (evita duplicar o histórico principal)
                    current = pending_lancamento.descricao
                    if stripped.upper() not in current.upper():
                        pending_lancamento = Lancamento(
                            data=pending_lancamento.data,
                            descricao=self._clean_description(
                                f"{current} | {stripped}"
                            ),
                            debito=pending_lancamento.debito,
                            credito=pending_lancamento.credito,
                        )

        # Confirma o último lançamento pendente
        _flush_pending()

        log.debug(
            f"BB: {self._result.total_lancamentos} lançamentos, "
            f"{self._result.total_ignoradas} ignoradas."
        )
        return self._result

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        p = date_str.split('/')
        if len(p[2]) == 2:
            p[2] = '20' + p[2]
        return '/'.join(p)


# ── Itaú ──────────────────────────────────────────────────────────────────────

class ItauParser(BankParser):
    """
    Parser para extratos do Itaú (pessoa física/jurídica via Internet Banking).

    Layout (pdfplumber extract_text com layout=True):
      - Linhas âncora:       5 espaços + DD/MM/AAAA + resto da transação
      - Linhas de continuação: 9+ espaços + fragmento (Razão Social, código etc.)

    Regra de sinais:
      - Valor com prefixo '-' → DÉBITO  (PIX ENVIADO, PAGAMENTOS, BOLETOS)
      - Valor sem prefixo     → CRÉDITO (PIX RECEBIDO, RECEBIMENTO REDE)
    """

    BANK_SIGNATURES = [
        "ITAU",
        "itaú",
        "Lançamentos do período",    # cabeçalho exclusivo do Itaú
        "Razão Social",              # coluna exclusiva do Itaú
        "SALDO TOTAL DISPONÍVEL DIA" # linha de saldo exclusiva do Itaú
    ]

    # Linha âncora: exatamente 4-8 espaços de recuo + data DD/MM/AAAA
    _ANCHOR_RE = re.compile(r'^ {4,8}(\d{2}/\d{2}/\d{4})\s+(.*?)\s*$')

    # Linha de continuação: 9+ espaços de recuo + conteúdo não-vazio
    _CONT_RE = re.compile(r'^ {9,}(\S.*?)\s*$')

    # Palavras-chave que identificam descrições de transação (vs. Razão Social)
    _KEYWORD_RE = re.compile(
        r'^(PIX|PAGAMENTOS|RECEBIMENTO|BOLETO|RENDIMENTOS|RECEBIMENTOS)\b',
        re.IGNORECASE,
    )

    # Linhas de saldo/cabeçalho a ignorar
    _SKIP_HIST = frozenset([
        'SALDO TOTAL DISPONÍVEL DIA', 'SALDO ANTERIOR',
        'DATA LANÇAMENTOS', 'VALOR (R$)', 'SALDO (R$)',
    ])

    # Remove CNPJ (00.000.000/0000-00) ou CPF (000.000.000-00) do final do desc
    _CNPJ_TRAIL = re.compile(
        r'\s+\d{2,3}[\d.]{5,}\d[-/]\d{4,6}-\d{2}\s*$'
    )

    def parse(self, full_text: str) -> ParseResult:
        self._result = ParseResult(banco="itau")
        lines = full_text.split('\n')
        total = len(lines)
        # Estima páginas contando ocorrências de "Agência" no cabeçalho
        pages = max(1, full_text.count('Agência'))
        self._result.total_linhas_pdf = total

        linhas_capturadas: set = set()
        pending: List[str] = []  # fragmentos de continuação acumulados

        for line_num, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip()
            if not line.strip():
                continue

            # ── Linha âncora (tem data) ───────────────────────────────────────
            a = self._ANCHOR_RE.match(line)
            if a:
                date_str = a.group(1)          # DD/MM/AAAA completo
                rest     = a.group(2).strip()

                # Ignora linhas de saldo e cabeçalho
                if any(s in rest.upper() for s in self._SKIP_HIST):
                    linhas_capturadas.add(line_num)
                    pending = []
                    continue

                # Extrai valor (último token numérico, possivelmente negativo)
                vm = re.search(r'(-?[\d.,]+)\s*$', rest)
                if not vm:
                    self._skip(line_num, line,
                               "Linha âncora sem valor numérico detectável",
                               total, pages)
                    pending = []
                    continue

                linhas_capturadas.add(line_num)
                valor_str = vm.group(1)
                desc_raw  = rest[:vm.start()].strip()

                # Remove CNPJ/CPF do final do desc_raw
                desc_raw = self._CNPJ_TRAIL.sub('', desc_raw).strip()
                # Segunda passagem para CPF simples que ficou residual
                desc_raw = re.sub(
                    r'\s+\d{3}\.\d{3}\.\d{3}-\d{2}\s*$', '', desc_raw
                ).strip()
                desc_raw = re.sub(
                    r'\s+\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s*$', '', desc_raw
                ).strip()

                # ── Escolha da melhor descrição ───────────────────────────────
                if self._KEYWORD_RE.match(desc_raw):
                    # A própria linha âncora tem uma descrição de transação
                    desc = desc_raw
                else:
                    # Procura no pending a última entrada que começa com keyword
                    kw_candidates = [
                        p for p in pending if self._KEYWORD_RE.match(p)
                    ]
                    if kw_candidates:
                        desc = kw_candidates[-1]
                    elif desc_raw:
                        desc = desc_raw
                    else:
                        desc = ' '.join(pending[:3]) if pending else '(sem descrição)'

                # ── Conversão do valor ────────────────────────────────────────
                negativo = valor_str.startswith('-')
                valor = parse_valor_br(valor_str.lstrip('-'))
                if valor is None:
                    self._skip(line_num, line,
                               f"Valor inválido: {valor_str!r}",
                               total, pages)
                    pending = []
                    continue

                self._result.lancamentos.append(Lancamento(
                    data=date_str,
                    descricao=self._clean_description(desc),
                    debito=valor  if negativo else None,
                    credito=valor if not negativo else None,
                ))
                pending = []
                continue

            # ── Linha de continuação (sem data) ──────────────────────────────
            c = self._CONT_RE.match(line)
            if c:
                pending.append(c.group(1).strip())

        # Pós-processamento: detecta padrões data+valor que escaparam
        self._varredura_pos_processamento(lines, linhas_capturadas, pages)

        log.debug(
            f"Itaú: {self._result.total_lancamentos} lançamentos, "
            f"{self._result.total_ignoradas} ignoradas."
        )
        return self._result


# ── Registro global ───────────────────────────────────────────────────────────

PARSERS: Dict[str, Type[BankParser]] = {
    "itau":      ItauParser,       # Itaú antes dos outros (assinaturas únicas)
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
