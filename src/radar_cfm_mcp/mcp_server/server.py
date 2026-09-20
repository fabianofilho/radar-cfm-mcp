"""Entrypoint MCP (stdio) do radar do CFM."""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from radar_cfm_mcp.config import carregar_config
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


def main() -> None:
    """Sobe o servidor MCP no stdio."""
    config = carregar_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("radar-cfm-mcp subindo (base em %s)", config.duckdb_path)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
