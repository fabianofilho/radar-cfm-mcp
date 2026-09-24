"""Entrypoint MCP (stdio) do radar do CFM."""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from radar_cfm_mcp.config import carregar_config
from radar_cfm_mcp.mcp_server.capacidades import esconder_o_que_nao_existe
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

# Tetos dos parametros das tools. O connector e publico: sem teto, uma chamada
# com limite enorme devolve a base inteira e um cliente em laco multiplica isso.
LIMITE_MAXIMO = 100
DIAS_MAXIMO = 3650


@mcp.tool()
async def consultar_resolucao_cfm(
    tema: str,
    apenas_vigentes: bool = True,
    limite: Annotated[int, Field(ge=1, le=LIMITE_MAXIMO)] = 10,
) -> RespostaConsulta:
    """Consulta resoluções do CFM relacionadas a um tema.

    Devolve as resoluções mais relevantes, com número, ano, data, ementa, o
    trecho em volta do termo buscado e sempre a URL de origem. Resoluções
    revogadas vêm com vigente=false e o número da que as substituiu.

    A resposta traz `total` (quantas casam na base) e `retornados` (quantas
    vieram). Com `truncado=true`, não conclua "só existem N resoluções sobre
    isso": aumente `limite`.

    `trecho_relevante=null` significa que o termo não aparece no texto, e não
    que a norma não trate do assunto. Nesse caso a resolução casou pela ementa
    ou pelo índice, então abra a URL antes de afirmar qualquer coisa.

    Nunca trate um trecho como "a posição do CFM" sem abrir a fonte: a ementa e
    o link vêm justamente para isso.

    Args:
        tema: assunto a buscar, por exemplo "telemedicina" ou "inteligência artificial".
        apenas_vigentes: quando True, omite as resoluções já revogadas.
        limite: quantas resoluções trazer, no máximo 100. Aumente para ver além das
            mais relevantes.
    """
    config = carregar_config()
    return await _consultar_resolucao_cfm(
        tema,
        caminho_db=str(config.duckdb_path),
        apenas_vigentes=apenas_vigentes,
        limite=limite,
    )


@mcp.tool()
async def monitorar_novas_resolucoes(
    dias: Annotated[int, Field(ge=1, le=DIAS_MAXIMO)] = 30,
    filtrar_tema: bool = True,
    limite: Annotated[int, Field(ge=1, le=LIMITE_MAXIMO)] = 50,
) -> RespostaMonitoramento:
    """Resoluções do CFM publicadas nos últimos dias, mais recentes primeiro.

    A janela usa a data de publicação extraída do texto da resolução. As que
    ficaram sem data não entram, e `sem_data_publicacao` diz quantas são: total
    zero com esse número alto quer dizer "não sei", não "nada foi publicado".

    A resposta traz `total` (quantas casam no período) e `retornados` (quantas
    vieram). Com `truncado=true`, aumente `limite` ou encurte `dias`.

    Args:
        dias: tamanho da janela, em dias, a contar de hoje (máximo 3650).
        filtrar_tema: quando True, devolve só as que mencionam, na ementa ou no
            texto, as palavras-chave configuradas no servidor (por padrão
            inteligência artificial, telemedicina, prontuário eletrônico e algoritmo).
        limite: quantas resoluções trazer, no máximo 100.
    """
    config = carregar_config()
    return await _monitorar_novas_resolucoes(
        dias,
        caminho_db=str(config.duckdb_path),
        palavras_chave=config.palavras_chave if filtrar_tema else (),
        limite=limite,
    )


def _seguranca_de_transporte(config: Any) -> Any:
    """Regras de Host/Origin quando o servidor atende por um nome publico.

    O SDK so liga a protecao contra DNS rebinding sozinho quando o bind e
    loopback, e ai aceita apenas Host de loopback. Atras de um tunel ou proxy o
    Host que chega e o nome publico, e a requisicao legitima levaria 421. Em vez
    de desligar a checagem, declaramos os nomes por onde o servidor responde.

    Devolve None quando nao ha nome publico configurado, deixando o padrao do
    SDK valer.
    """
    if not config.http_hosts_publicos:
        return None

    from mcp.server.transport_security import TransportSecuritySettings

    hosts: list[str] = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origens: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]
    for nome in config.http_hosts_publicos:
        # Com e sem porta: atras de TLS o Host costuma vir sem o ":443".
        hosts += [nome, f"{nome}:*"]
        origens += [f"https://{nome}", f"https://{nome}:*"]
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origens)


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
    # Este servidor so tem tools. Anunciar prompts e resources faria quem mapeia
    # o servidor gastar chamadas para descobrir lista vazia.
    esconder_o_que_nao_existe(mcp)
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
            transport_security=_seguranca_de_transporte(config),
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
