"""As duas tools: consultar por tema e monitorar novas resoluções."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from radar_cfm_mcp.store.db import BaseIndisponivel, conectar
from radar_cfm_mcp.store.queries import (
    buscar_por_tema,
    contar_por_tema,
    publicadas_no_periodo,
)

logger = logging.getLogger(__name__)

AVISO_BASE_VAZIA = (
    "A base local ainda não foi sincronizada com o portal do CFM. "
    "Rode 'cfm-cli sync' antes de consultar."
)
AVISO_BASE_TRAVADA = (
    "A base local existe mas não pôde ser lida agora, provavelmente há um sync em "
    "andamento. Tente de novo em alguns minutos."
)
AVISO_FONTE = (
    "Cada resultado traz a ementa e a URL de origem: confira o texto oficial antes de "
    "tratar qualquer trecho como a posição do CFM."
)


AVISO_SO_EMENTA = (
    "Nenhum destes resultados tem o texto integral na base, então a busca comparou só "
    "a ementa. O projeto baixa o PDF apenas das resoluções cuja ementa toca em IA ou "
    "telemedicina, que é o escopo dele. Para os demais temas, o conteúdo dos artigos "
    "não foi lido: abra a URL antes de afirmar o que a norma diz."
)
AVISO_BUSCA_POR_PALAVRA = (
    "A busca casa qualquer palavra do tema, não a frase inteira, então um tema com "
    "várias palavras infla o total com resoluções que só têm uma delas. Trate o total "
    "como 'quantas mencionam alguma dessas palavras', não como 'quantas tratam disso'."
)


class ResolucaoCFM(BaseModel):
    """Uma resolução, sempre com a fonte para conferência."""

    identificador: str = Field(description="Número/ano, por exemplo 2314/2022")
    numero: str
    ano: str
    data_publicacao: date | None = None
    ementa: str | None = None
    trecho_relevante: str | None = Field(
        default=None,
        description=(
            "Trecho do texto integral em volta do termo buscado. None por dois "
            "motivos diferentes, distinguidos por 'texto_completo_disponivel': se "
            "for false, o texto integral não foi baixado e a busca viu só a ementa; "
            "se for true, o termo não aparece no texto. Em nenhum dos casos conclua "
            "que a norma não trata do assunto, abra a URL de origem."
        ),
    )
    vigente: bool
    revogada_por: str | None = None
    url_origem: str
    url_pdf: str
    texto_completo_disponivel: bool
    metadados_incompletos: bool = False


class RespostaConsulta(BaseModel):
    tema: str
    total: int = Field(
        description=(
            "Quantas resoluções casam com o tema na base inteira, não quantas vieram "
            "nesta resposta. Temas amplos casam com dezenas."
        )
    )
    retornados: int = Field(
        default=0, description="Quantas vieram em 'resultados', no máximo 'limite'"
    )
    truncado: bool = Field(
        default=False,
        description=(
            "True quando total > retornados. As que vieram são as mais relevantes; "
            "as demais existem e não estão aqui. Não conclua 'só existem N resoluções "
            "sobre isso' a partir da lista."
        ),
    )
    resultados: list[ResolucaoCFM]
    aviso: str | None = None


class RespostaMonitoramento(BaseModel):
    dias: int
    total: int
    resultados: list[ResolucaoCFM]
    aviso: str | None = None


def _trecho(texto: str | None, termo: str, *, janela: int = 260) -> str | None:
    """Pedaço do texto em volta da primeira ocorrência do termo, ou None.

    Devolver o começo do documento quando o termo não aparece seria pior que
    devolver nada: o campo se chama ``trecho_relevante`` e quem lê trata como
    resposta à pergunta feita. A abertura de uma resolução é sempre plausível,
    então o erro passa despercebido e vira citação errada.

    Busca o termo inteiro e, se não achar, as palavras dele da mais longa para a
    mais curta, porque quem pergunta escreve frase ("telediagnóstico assíncrono")
    e a norma traz uma palavra só.

    Tolera a desinência final: a pergunta vem com "assíncrono" e o texto diz
    "assíncrona". O corte para no radical de cinco letras, senão sobra um prefixo
    curto que casa em qualquer palavra e devolve trecho aleatório, que é o mesmo
    defeito por outro caminho.
    """
    if not texto:
        return None

    alvo = texto.lower()
    posicao = alvo.find(termo.strip().lower())
    if posicao < 0:
        palavras = sorted(
            (p for p in re.split(r"\W+", termo.lower()) if len(p) >= 4),
            key=len,
            reverse=True,
        )
        for palavra in palavras:
            for corte in (palavra, palavra[:-1], palavra[:-2]):
                if len(corte) < 5 and corte != palavra:
                    continue
                posicao = alvo.find(corte)
                if posicao >= 0:
                    break
            if posicao >= 0:
                break
    if posicao < 0:
        return None

    inicio = max(0, posicao - janela // 2)
    fim = min(len(texto), posicao + janela // 2)
    prefixo = "…" if inicio > 0 else ""
    sufixo = "…" if fim < len(texto) else ""
    return f"{prefixo}{texto[inicio:fim].strip()}{sufixo}"


def _para_modelo(linha: dict[str, Any], termo: str | None = None) -> ResolucaoCFM:
    texto = linha.get("texto_completo")
    return ResolucaoCFM(
        identificador=linha["identificador"],
        numero=linha["numero"],
        ano=linha["ano"],
        data_publicacao=linha.get("data_publicacao"),
        ementa=linha.get("ementa") or None,
        trecho_relevante=_trecho(texto, termo) if termo else None,
        vigente=bool(linha.get("vigente", True)),
        revogada_por=linha.get("revogada_por"),
        url_origem=linha["url_origem"],
        url_pdf=linha["url_pdf"],
        texto_completo_disponivel=bool(texto),
        metadados_incompletos=bool(linha.get("metadados_incompletos", False)),
    )


def _ler[T](
    caminho_db: str,
    funcao: Callable[[duckdb.DuckDBPyConnection], T],
    *,
    padrao: T,
) -> tuple[T, str | None]:
    """Abre a base em leitura e roda a consulta. Nunca levanta.

    ``padrao`` é o que volta quando a base não pôde ser lida. Quem chama precisa
    dizer qual é, porque as consultas devolvem formatos diferentes e devolver
    lista vazia para quem espera uma tupla quebra só em produção.
    """
    try:
        with conectar(caminho_db, somente_leitura=True) as conexao:
            return funcao(conexao), None
    except FileNotFoundError:
        return padrao, AVISO_BASE_VAZIA
    except BaseIndisponivel as erro:
        logger.warning("base indisponível: %s", erro)
        return padrao, AVISO_BASE_TRAVADA
    except Exception:  # noqa: BLE001 - nenhuma falha de base derruba a tool
        logger.exception("falha ao consultar a base")
        return padrao, AVISO_BASE_TRAVADA


async def consultar_resolucao_cfm(
    tema: str,
    *,
    caminho_db: str,
    limite: int = 10,
    apenas_vigentes: bool = True,
) -> RespostaConsulta:
    """Resoluções relacionadas ao tema, mais relevantes primeiro."""
    if not tema.strip():
        return RespostaConsulta(tema=tema, total=0, resultados=[], aviso="Informe um tema.")

    def consultar(c: duckdb.DuckDBPyConnection) -> tuple[list[dict[str, Any]], int]:
        return (
            buscar_por_tema(c, tema, limite=limite, apenas_vigentes=apenas_vigentes),
            contar_por_tema(c, tema, apenas_vigentes=apenas_vigentes),
        )

    # Anotado porque ([], 0) sozinho faz o mypy inferir list[Never].
    vazio: tuple[list[dict[str, Any]], int] = ([], 0)
    (linhas, total), aviso = _ler(caminho_db, consultar, padrao=vazio)

    truncado = total > len(linhas)
    avisos = [aviso or AVISO_FONTE]
    if truncado:
        avisos.append(
            f"Casaram {total} resoluções e estão aqui as {len(linhas)} mais relevantes. "
            f"Não conclua que só existem {len(linhas)} sobre o tema; aumente 'limite' "
            f"para ver mais."
        )
    if len(tema.split()) > 1 and truncado:
        avisos.append(AVISO_BUSCA_POR_PALAVRA)
    if linhas and not any(linha.get("texto_completo") for linha in linhas):
        avisos.append(AVISO_SO_EMENTA)
    return RespostaConsulta(
        tema=tema,
        total=total,
        retornados=len(linhas),
        truncado=truncado,
        resultados=[_para_modelo(linha, tema) for linha in linhas],
        aviso=" ".join(avisos),
    )


async def monitorar_novas_resolucoes(
    dias: int = 30,
    *,
    caminho_db: str,
    palavras_chave: tuple[str, ...] = (),
    limite: int = 50,
) -> RespostaMonitoramento:
    """Resoluções publicadas no período, filtradas por relevância ao tema de IA."""
    if dias <= 0:
        return RespostaMonitoramento(
            dias=dias,
            total=0,
            resultados=[],
            aviso="O parâmetro 'dias' precisa ser maior que zero.",
        )

    nenhuma: list[dict[str, Any]] = []
    linhas, aviso = _ler(
        caminho_db,
        lambda c: publicadas_no_periodo(c, dias=dias, limite=limite),
        padrao=nenhuma,
    )

    if palavras_chave:
        alvos = tuple(p.lower() for p in palavras_chave if p.strip())
        linhas = [
            linha
            for linha in linhas
            if any(
                alvo in f"{linha.get('ementa') or ''} {linha.get('texto_completo') or ''}".lower()
                for alvo in alvos
            )
        ]

    return RespostaMonitoramento(
        dias=dias,
        total=len(linhas),
        resultados=[_para_modelo(linha) for linha in linhas],
        aviso=aviso or AVISO_FONTE,
    )
