"""CLI de administração: crawl manual e teste das tools."""

from __future__ import annotations

import asyncio
import json

import typer

from radar_cfm_mcp.config import carregar_config
from radar_cfm_mcp.crawler.sync import sincronizar
from radar_cfm_mcp.mcp_server.tools.resolucoes import (
    consultar_resolucao_cfm,
    monitorar_novas_resolucoes,
)
from radar_cfm_mcp.store.db import conectar, reindexar_fts

app = typer.Typer(help="Administração do radar-cfm-mcp", no_args_is_help=True)


@app.command()
def sync(
    max_paginas: int = typer.Option(0, help="0 = todas; útil para testar com poucas"),
    max_pdfs: int = typer.Option(25, help="Teto de PDFs baixados nesta execução"),
) -> None:
    """Varre o portal do CFM e atualiza a base local."""
    config = carregar_config()
    logging_level = config.log_level.upper()
    import logging

    logging.basicConfig(
        level=getattr(logging, logging_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    async def rodar() -> None:
        with conectar(config.duckdb_path) as conexao:
            resultado = await sincronizar(
                conexao,
                palavras_chave=config.palavras_chave,
                delay_segundos=config.crawler_delay_segundos,
                max_paginas=max_paginas or None,
                max_pdfs=max_pdfs,
                diretorio_cache=config.duckdb_path.parent / "pdfs",
            )
            indexado = reindexar_fts(conexao)
            typer.echo(
                f"{resultado.novos} novas, {resultado.atualizados} atualizadas, "
                f"{resultado.pdfs_baixados} PDFs baixados"
            )
            typer.echo(f"índice FTS: {'criado' if indexado else 'indisponível (busca por LIKE)'}")

    asyncio.run(rodar())


@app.command()
def consultar(tema: str, todas: bool = typer.Option(False, help="Inclui revogadas")) -> None:
    """Testa a tool de consulta fora do MCP."""
    config = carregar_config()
    resposta = asyncio.run(
        consultar_resolucao_cfm(tema, caminho_db=str(config.duckdb_path), apenas_vigentes=not todas)
    )
    typer.echo(resposta.model_dump_json(indent=2))


@app.command()
def novas(dias: int = 30) -> None:
    """Testa a tool de monitoramento fora do MCP."""
    config = carregar_config()
    resposta = asyncio.run(
        monitorar_novas_resolucoes(
            dias, caminho_db=str(config.duckdb_path), palavras_chave=config.palavras_chave
        )
    )
    typer.echo(resposta.model_dump_json(indent=2))


@app.command()
def schema() -> None:
    """Contagens da base local."""
    config = carregar_config()
    with conectar(config.duckdb_path) as conexao:
        total = conexao.execute("SELECT count(*) FROM resolucoes").fetchone()
        com_texto = conexao.execute(
            "SELECT count(*) FROM resolucoes WHERE texto_completo IS NOT NULL"
        ).fetchone()
        vigentes = conexao.execute("SELECT count(*) FROM resolucoes WHERE vigente").fetchone()
    typer.echo(
        json.dumps(
            {
                "resolucoes": int(total[0]) if total else 0,
                "vigentes": int(vigentes[0]) if vigentes else 0,
                "com_texto_completo": int(com_texto[0]) if com_texto else 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
