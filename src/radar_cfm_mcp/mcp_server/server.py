"""Entrypoint MCP (stdio) do radar do CFM."""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from radar_cfm_mcp.config import carregar_config
from radar_cfm_mcp.mcp_server.limite import LimitadorPorOrigem, origem_da_requisicao
from radar_cfm_mcp.mcp_server.tools.resolucoes import RespostaConsulta, RespostaMonitoramento
from radar_cfm_mcp.mcp_server.tools.resolucoes import (
    consultar_resolucao_cfm as _consultar_resolucao_cfm,
)
from radar_cfm_mcp.mcp_server.tools.resolucoes import (
    monitorar_novas_resolucoes as _monitorar_novas_resolucoes,
)

logger = logging.getLogger(__name__)

mcp = MCPServer("radar-cfm-mcp", version="0.1.0")


@mcp.tool()
async def consultar_resolucao_cfm(tema: str, apenas_vigentes: bool = True) -> RespostaConsulta:
    """Consulta resoluções do CFM relacionadas a um tema.

    Devolve as resoluções mais relevantes, com número, ano, data, ementa, o
    trecho em volta do termo buscado e sempre a URL de origem. Resoluções
    revogadas vêm com vigente=false e o número da que as substituiu.

    Nunca trate um trecho como "a posição do CFM" sem abrir a fonte: a ementa e
    o link vêm justamente para isso.

    Args:
        tema: assunto a buscar, por exemplo "telemedicina" ou "inteligência artificial".
        apenas_vigentes: quando True, omite as resoluções já revogadas.
    """
    config = carregar_config()
    return await _consultar_resolucao_cfm(
        tema, caminho_db=str(config.duckdb_path), apenas_vigentes=apenas_vigentes
    )


@mcp.tool()
async def monitorar_novas_resolucoes(
    dias: int = 30, filtrar_tema: bool = True
) -> RespostaMonitoramento:
    """Resoluções do CFM publicadas nos últimos dias.

    Args:
        dias: tamanho da janela, em dias, a contar de hoje.
        filtrar_tema: quando True, devolve só as que mencionam os temas
            configurados (IA, telemedicina, prontuário eletrônico, algoritmo).
    """
    config = carregar_config()
    return await _monitorar_novas_resolucoes(
        dias,
        caminho_db=str(config.duckdb_path),
        palavras_chave=config.palavras_chave if filtrar_tema else (),
    )


def _com_limite(app: Any, limite_por_minuto: int, limite_global: int) -> Any:
    """Embrulha o app ASGI com o teto de requisicoes por origem.

    O `run()` do SDK nao aceita middleware, entao o app e construido por
    `streamable_http_app()`, embrulhado aqui e servido por uvicorn.
    """
    limitador = LimitadorPorOrigem(limite_por_minuto, limite_global)

    async def middleware(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return
        origem = origem_da_requisicao(scope)
        if not limitador.permitir(origem):
            logger.warning("limite %s excedido (origem %s)", limitador.motivo_ultima_recusa, origem)
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"60"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"erro":"limite de requisicoes excedido; tente em 1 minuto"}',
                }
            )
            return
        await app(scope, receive, send)

    return middleware


def main() -> None:
    """Sobe o servidor MCP. Stdio por padrao; HTTP no modo connector."""
    config = carregar_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if config.transporte == "stdio":
        logger.info("radar-cfm-mcp subindo em stdio (base em %s)", config.duckdb_path)
        mcp.run(transport="stdio")
        return

    # Modo connector. A base e aberta somente para leitura a cada requisicao, e
    # o sync roda FORA deste processo: um escritor com leitor aberto e recusado
    # pelo DuckDB, entao a coleta grava num arquivo novo e troca por rename.
    import uvicorn

    app = _com_limite(
        mcp.streamable_http_app(
            streamable_http_path=config.http_path,
            stateless_http=config.http_stateless,
            host=config.http_host,
        ),
        config.http_limite_por_minuto,
        config.http_limite_global_por_minuto,
    )
    logger.info(
        "radar-cfm-mcp em http://%s:%d%s (stateless=%s, limite %d/min por origem e "
        "%d/min global, base=%s)",
        config.http_host,
        config.http_porta,
        config.http_path,
        config.http_stateless,
        config.http_limite_por_minuto,
        config.http_limite_global_por_minuto,
        config.duckdb_path,
    )
    uvicorn.run(app, host=config.http_host, port=config.http_porta, log_level="warning")


if __name__ == "__main__":
    main()
