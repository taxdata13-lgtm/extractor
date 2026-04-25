#!/usr/bin/env python3
"""
Conversor Contábil CLI — Extrai extratos bancários (PDF) para Excel (.xlsx).

Uso:
    python conversor.py --input extrato.pdf --output planilha.xlsx
    python conversor.py --input extrato.pdf --output planilha.xlsx --banco santander
    python conversor.py --input extrato.pdf --output planilha.xlsx --debug
"""

import argparse
import sys
import logging
from pathlib import Path

from banks import PARSERS, detect_bank
from exporter import export_to_excel
from extractor import extract_text_from_pdf


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conversor",
        description="Converte extratos bancários em PDF para planilhas Excel (.xlsx).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Bancos suportados: {', '.join(PARSERS.keys())}",
    )
    parser.add_argument("--input",  "-i", required=True, metavar="ARQUIVO.pdf",
                        help="Caminho para o arquivo PDF do extrato bancário.")
    parser.add_argument("--output", "-o", required=True, metavar="PLANILHA.xlsx",
                        help="Caminho de saída para o arquivo Excel gerado.")
    parser.add_argument("--banco",  "-b", choices=list(PARSERS.keys()), default=None,
                        metavar="BANCO",
                        help=f"Força o parser de um banco. Opções: {', '.join(PARSERS.keys())}")
    parser.add_argument("--debug", action="store_true",
                        help="Exibe texto bruto extraído do PDF para depuração.")
    return parser


def _print_report(result, log) -> None:
    """Imprime o relatório de processamento no terminal."""
    sep = "─" * 60

    log.info(sep)
    log.info("RELATÓRIO DE PROCESSAMENTO")
    log.info(sep)
    log.info(f"  Banco identificado   : {result.banco.upper()}")
    log.info(f"  Linhas no PDF        : {result.total_linhas_pdf:>6}")
    log.info(f"  Lançamentos extraídos: {result.total_lancamentos:>6}")
    log.info(f"  Linhas ignoradas     : {result.total_ignoradas:>6}")
    log.info(f"  Taxa de sucesso      : {result.taxa_sucesso:>5.1f}%")
    log.info(sep)
    log.info(f"  Total débitos (R$)   : {result.total_debitos:>15,.2f}")
    log.info(f"  Total créditos (R$)  : {result.total_creditos:>15,.2f}")
    log.info(f"  Saldo do período (R$): {result.total_creditos - result.total_debitos:>15,.2f}")
    log.info(sep)

    if result.total_ignoradas == 0:
        log.info("  ✓ Nenhuma linha foi ignorada. Extração completa.")
        log.info(sep)
        return

    # Agrupa e exibe motivos
    motivos = result.motivos_agrupados()
    log.warning(f"  {result.total_ignoradas} linha(s) foram ignoradas. Motivos:")
    for motivo, count in motivos.items():
        log.warning(f"    [{count:>3}x] {motivo}")
    log.info(sep)
    log.info("  ▶ Detalhes completos na aba 'Linhas Ignoradas' do Excel gerado.")
    log.info(sep)


def main() -> int:
    args = build_arg_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    log = logging.getLogger("conversor")

    # ── Validações de entrada ─────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        log.error(f"Arquivo não encontrado: {input_path}")
        return 1
    if input_path.suffix.lower() != ".pdf":
        log.error(f"O arquivo de entrada deve ser um PDF: {input_path}")
        return 1

    output_path = Path(args.output)
    if output_path.suffix.lower() != ".xlsx":
        log.warning("A extensão de saída foi corrigida para .xlsx")
        output_path = output_path.with_suffix(".xlsx")

    # ── Extração de texto ─────────────────────────────────────────────────────
    log.info(f"Lendo PDF: {input_path}")
    pages_text = extract_text_from_pdf(input_path)
    full_text = "\n".join(pages_text)

    if args.debug:
        log.debug("=== TEXTO BRUTO EXTRAÍDO ===\n" + full_text)

    if not full_text.strip():
        log.error("Nenhum texto pôde ser extraído do PDF. Verifique se não é um PDF escaneado sem OCR.")
        return 1

    # ── Identificação do banco ────────────────────────────────────────────────
    banco_key = args.banco
    if banco_key:
        log.info(f"Banco especificado manualmente: {banco_key}")
    else:
        banco_key = detect_bank(full_text)
        if banco_key:
            log.info(f"Banco identificado automaticamente: {banco_key}")
        else:
            log.error(
                "Não foi possível identificar o banco automaticamente.\n"
                f"Use a flag --banco com uma das opções: {', '.join(PARSERS.keys())}"
            )
            return 1

    # ── Parsing dos lançamentos ───────────────────────────────────────────────
    parser_cls = PARSERS[banco_key]
    parser_instance = parser_cls()
    log.info(f"Processando lançamentos com parser: {parser_cls.__name__}")

    result = parser_instance.parse(full_text)

    if not result.lancamentos:
        log.error("Nenhum lançamento foi encontrado. Verifique o arquivo ou use --debug.")
        return 1

    # ── Relatório no terminal ─────────────────────────────────────────────────
    _print_report(result, log)

    # ── Exportação para Excel ─────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_to_excel(result, output_path)
    log.info(f"Planilha gerada com sucesso: {output_path.resolve()}")

    # Código de saída: 0 = sucesso total, 2 = sucesso com avisos
    return 2 if result.total_ignoradas > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

