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
from radar_cfm_mcp.store.troca import BaseSuspeita, clonar_para_construcao, publicar

app = typer.Typer(help="Administração do radar-cfm-mcp", no_args_is_help=True)


@app.command()
def sync(
    max_paginas: int = typer.Option(0, help="0 = todas; útil para testar com poucas"),
    max_pdfs: int = typer.Option(25, help="Teto de PDFs baixados nesta execução"),
    texto_integral: bool = typer.Option(
        False,
        "--texto-integral",
        help="Baixa o PDF de TODAS as resoluções, não só das que tocam no tema",
    ),
    publicar_ao_fim: bool = typer.Option(
        False,
        "--publicar",
        help="Constrói a base ao lado e troca por rename no fim (modo connector)",
    ),
    forcar: bool = typer.Option(
        False,
        "--forcar",
        help="Publica mesmo se a base nova encolheu (primeiro carregamento ou teste)",
    ),
) -> None:
    """Varre o portal do CFM e atualiza a base local.

    Com ``--publicar``, escreve numa base nova e só troca pela servida no fim.
    É o modo para quando há um servidor HTTP lendo o arquivo: o DuckDB recusa
    abrir para escrita enquanto houver leitor, então escrever direto falharia.

    ``--texto-integral`` baixa o PDF de todas as 2.457 resoluções, e não só das
    que tocam em IA ou telemedicina. Sem ele, a busca por tema compara apenas a
    ementa. Com o intervalo padrão entre requisições, a primeira execução leva
    perto de uma hora e meia; o cache em disco evita repetir depois. Combine com
    ``--max-pdfs 0`` para não parar no teto.
    """
    config = carregar_config()
    logging_level = config.log_level.upper()
    import logging

    logging.basicConfig(
        level=getattr(logging, logging_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    alvo = clonar_para_construcao(config.duckdb_path) if publicar_ao_fim else config.duckdb_path

    async def rodar() -> None:
        with conectar(alvo) as conexao:
            resultado = await sincronizar(
                conexao,
                palavras_chave=config.palavras_chave,
                delay_segundos=config.crawler_delay_segundos,
                max_paginas=max_paginas or None,
                max_pdfs=max_pdfs if max_pdfs > 0 else 10**9,
                texto_integral=texto_integral,
                diretorio_cache=config.duckdb_path.parent / "pdfs",
            )
            indexado = reindexar_fts(conexao)
            typer.echo(
                f"{resultado.novos} novas, {resultado.atualizados} atualizadas, "
                f"{resultado.pdfs_baixados} PDFs baixados"
            )
            typer.echo(f"índice FTS: {'criado' if indexado else 'indisponível (busca por LIKE)'}")

        if publicar_ao_fim:
            try:
                publicado = publicar(config.duckdb_path, forcar=forcar)
            except BaseSuspeita as erro:
                typer.secho(f"publicação recusada: {erro}", fg=typer.colors.RED)
                raise typer.Exit(code=1) from erro
            typer.secho(f"base publicada: {publicado}", fg=typer.colors.GREEN)

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
