"""Sincronização: varre a busca, baixa PDFs dos relevantes, grava e indexa."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from radar_cfm_mcp.crawler.cache import CachePdf
from radar_cfm_mcp.crawler.search import CrawlerCFM, Resolucao
from radar_cfm_mcp.extract.datas import extrair_data_publicacao
from radar_cfm_mcp.extract.parser import casa_palavras_chave, extrair_pdf
from radar_cfm_mcp.store.queries import gravar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoSync:
    novos: int
    atualizados: int
    pdfs_baixados: int
    # Datas preenchidas ou corrigidas a partir do texto que ja estava na base.
    datas_reextraidas: int = 0

    @property
    def total(self) -> int:
        return self.novos + self.atualizados


def _registro(resolucao: Resolucao, **extra: Any) -> dict[str, Any]:
    texto = extra.get("texto_completo")
    # O campo DATA do portal e a data de carga no sistema deles, nao a
    # publicacao: resolucao de 1972 vem com 01/11/24. A data boa esta no texto.
    data = extrair_data_publicacao(texto, resolucao.ano)
    return {
        "identificador": resolucao.identificador,
        "numero": resolucao.numero,
        "ano": resolucao.ano,
        "data_publicacao": data,
        "ementa": resolucao.ementa,
        "texto_completo": texto,
        "vigente": resolucao.vigente,
        "revogada_por": resolucao.revogada_por,
        "url_origem": resolucao.url_origem,
        "url_pdf": resolucao.url_pdf,
        "metadados_incompletos": bool(extra.get("metadados_incompletos", False)),
    }


async def sincronizar(
    conexao: duckdb.DuckDBPyConnection,
    *,
    palavras_chave: tuple[str, ...],
    delay_segundos: float = 2.0,
    max_paginas: int | None = None,
    max_pdfs: int = 25,
    diretorio_cache: Path | str = "./data/pdfs",
    texto_integral: bool = False,
) -> ResultadoSync:
    """Varre a busca e baixa o texto completo dos relevantes.

    Por padrão o PDF só é baixado quando a ementa casa com alguma palavra-chave:
    são 2.457 resoluções, e baixar todas de uma vez é pesado para um portal de
    conselho profissional.

    Com ``texto_integral``, baixa o de todas. Vale quando quem hospeda vai servir
    a base a mais gente, porque aí o download acontece uma vez só e poupa cada
    usuário de repetir a varredura. O cache em disco evita rebaixar o que já veio,
    então o custo alto é o da primeira execução.

    Sem o texto, a busca por tema compara só a ementa, e quem consulta recebe
    "trecho não encontrado" sem saber se a norma trata do assunto ou se o texto
    nunca foi lido.
    """
    cache = CachePdf(diretorio_cache)
    baixados = 0
    # Quem ja tem texto na base nao precisa do PDF de novo: o upsert preserva o
    # texto e a data quando a coleta traz nulo. Sem isto, o sync diario com
    # --texto-integral releria as 2.457 do cache toda noite. A marca de
    # extracao incompleta vai junto, senao a coleta a apagaria.
    ja_lidas: dict[str, bool] = dict(
        conexao.execute(
            "SELECT identificador, metadados_incompletos FROM resolucoes "
            "WHERE texto_completo IS NOT NULL AND texto_completo <> ''"
        ).fetchall()
    )

    async with CrawlerCFM(delay_segundos=delay_segundos) as crawler:
        resolucoes = await crawler.varrer(max_paginas=max_paginas)
        logger.info("CFM: %d resoluções coletadas", len(resolucoes))
        if texto_integral:
            logger.info("CFM: baixando o PDF de todas, teto de %d nesta execução", max_pdfs)

        registros: list[dict[str, Any]] = []
        for resolucao in resolucoes:
            if resolucao.identificador in ja_lidas:
                registros.append(
                    _registro(resolucao, metadados_incompletos=ja_lidas[resolucao.identificador])
                )
                continue
            relevante = texto_integral or casa_palavras_chave(resolucao.ementa, palavras_chave)
            if not relevante:
                registros.append(_registro(resolucao))
                continue

            conteudo = cache.ler(resolucao.url_pdf)
            if conteudo is None and baixados < max_pdfs:
                try:
                    conteudo = await crawler.baixar_pdf(resolucao)
                    cache.gravar(resolucao.url_pdf, conteudo)
                    baixados += 1
                except Exception as erro:  # noqa: BLE001 - um PDF ruim não para o sync
                    logger.warning("PDF de %s falhou: %s", resolucao.identificador, erro)
                    registros.append(_registro(resolucao, metadados_incompletos=True))
                    continue

            if conteudo is None:
                registros.append(_registro(resolucao))
                continue

            extraido = extrair_pdf(conteudo)
            registros.append(
                _registro(
                    resolucao,
                    texto_completo=extraido.texto or None,
                    metadados_incompletos=extraido.metadados_incompletos,
                )
            )

    novos, atualizados = gravar(conexao, registros)
    reextraidas = sum(reextrair_datas(conexao))
    logger.info(
        "CFM: %d novos, %d atualizados, %d PDFs baixados, %d datas reextraidas",
        novos,
        atualizados,
        baixados,
        reextraidas,
    )
    return ResultadoSync(
        novos=novos,
        atualizados=atualizados,
        pdfs_baixados=baixados,
        datas_reextraidas=reextraidas,
    )


def reextrair_datas(conexao: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Refaz a data de publicacao a partir do texto que ja esta na base.

    O extrator melhora com o tempo, mas a data so era calculada quando o PDF
    passava pelo parser, e o sync nao rele o que ja tem texto. Sem este passo,
    uma correcao no extrator nunca chegaria as resolucoes antigas. Roda sobre o
    texto gravado, sem tocar no portal.

    So escreve quando o extrator acha uma data: ausencia nao apaga data que ja
    existia. Devolve (preenchidas, corrigidas), onde corrigida e a que tinha
    data diferente, tipicamente a de outra norma citada no cabecalho.
    """
    linhas = conexao.execute(
        "SELECT identificador, ano, data_publicacao, texto_completo FROM resolucoes "
        "WHERE texto_completo IS NOT NULL AND texto_completo <> ''"
    ).fetchall()
    preenchidas = corrigidas = 0
    mudancas: list[tuple[date, str]] = []
    for identificador, ano, atual, texto in linhas:
        nova = extrair_data_publicacao(texto, ano)
        if nova is None or nova == atual:
            continue
        mudancas.append((nova, identificador))
        if atual is None:
            preenchidas += 1
        else:
            corrigidas += 1
    if mudancas:
        conexao.executemany(
            "UPDATE resolucoes SET data_publicacao = ? WHERE identificador = ?", mudancas
        )
    return preenchidas, corrigidas
