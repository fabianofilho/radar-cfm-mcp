"""Sincronização: varre a busca, baixa PDFs dos relevantes, grava e indexa."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from radar_cfm_mcp.crawler.cache import CachePdf
from radar_cfm_mcp.crawler.search import CrawlerCFM, Resolucao
from radar_cfm_mcp.extract.parser import casa_palavras_chave, extrair_pdf
from radar_cfm_mcp.store.queries import gravar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoSync:
    novos: int
    atualizados: int
    pdfs_baixados: int

    @property
    def total(self) -> int:
        return self.novos + self.atualizados


def _registro(resolucao: Resolucao, **extra: Any) -> dict[str, Any]:
    return {
        "identificador": resolucao.identificador,
        "numero": resolucao.numero,
        "ano": resolucao.ano,
        "data_publicacao": resolucao.data_publicacao,
        "ementa": resolucao.ementa,
        "texto_completo": extra.get("texto_completo"),
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
) -> ResultadoSync:
    """Varre a busca e baixa o texto completo só dos relevantes.

    O PDF só é baixado quando a ementa casa com alguma palavra-chave: são 2.457
    resoluções, e baixar todas seria abusivo com um portal de conselho.
    """
    cache = CachePdf(diretorio_cache)
    baixados = 0

    async with CrawlerCFM(delay_segundos=delay_segundos) as crawler:
        resolucoes = await crawler.varrer(max_paginas=max_paginas)
        logger.info("CFM: %d resoluções coletadas", len(resolucoes))

        registros: list[dict[str, Any]] = []
        for resolucao in resolucoes:
            relevante = casa_palavras_chave(resolucao.ementa, palavras_chave)
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
    logger.info("CFM: %d novos, %d atualizados, %d PDFs baixados", novos, atualizados, baixados)
    return ResultadoSync(novos=novos, atualizados=atualizados, pdfs_baixados=baixados)


def agendar_sync(caminho_db: str, hora_local: str, palavras_chave: tuple[str, ...]) -> Any:
    """Agenda a varredura num horário fixo e devolve o scheduler iniciado."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from radar_cfm_mcp.store.db import conectar, reindexar_fts

    async def tarefa() -> None:
        try:
            with conectar(caminho_db) as conexao:
                await sincronizar(conexao, palavras_chave=palavras_chave)
                reindexar_fts(conexao)
        except Exception:  # noqa: BLE001 - o agendador não pode morrer por um sync
            logger.exception("sync do CFM falhou")

    hora, minuto = (int(p) for p in hora_local.split(":"))
    scheduler = AsyncIOScheduler()
    scheduler.add_job(tarefa, "cron", hour=hora, minute=minuto)
    scheduler.start()
    logger.info("sync do CFM agendado diariamente às %s", hora_local)
    return scheduler
