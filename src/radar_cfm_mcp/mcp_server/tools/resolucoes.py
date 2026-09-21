"""As duas tools: consultar por tema e monitorar novas resoluções."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from radar_cfm_mcp.store.db import BaseIndisponivel, conectar
from radar_cfm_mcp.store.queries import buscar_por_tema, publicadas_no_periodo

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


class ResolucaoCFM(BaseModel):
    """Uma resolução, sempre com a fonte para conferência."""

    identificador: str = Field(description="Número/ano, por exemplo 2314/2022")
    numero: str
    ano: str
    data_publicacao: date | None = None
    ementa: str | None = None
    trecho_relevante: str | None = Field(
        default=None, description="Trecho do texto completo em volta do termo buscado"
    )
    vigente: bool
    revogada_por: str | None = None
    url_origem: str
    url_pdf: str
    texto_completo_disponivel: bool
    metadados_incompletos: bool = False


class RespostaConsulta(BaseModel):
    tema: str
    total: int
    resultados: list[ResolucaoCFM]
    aviso: str | None = None


class RespostaMonitoramento(BaseModel):
    dias: int
    total: int
    resultados: list[ResolucaoCFM]
    aviso: str | None = None


def _trecho(texto: str | None, termo: str, *, janela: int = 260) -> str | None:
    """Pedaço do texto em volta da primeira ocorrência do termo."""
    if not texto:
        return None
    posicao = texto.lower().find(termo.strip().lower())
    if posicao < 0:
        return texto[:janela].strip() + ("…" if len(texto) > janela else "")
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


def _ler(caminho_db: str, funcao: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Abre a base em leitura e roda a consulta. Nunca levanta."""
    try:
        with conectar(caminho_db, somente_leitura=True) as conexao:
            return funcao(conexao), None
    except FileNotFoundError:
        return [], AVISO_BASE_VAZIA
    except BaseIndisponivel as erro:
        logger.warning("base indisponível: %s", erro)
        return [], AVISO_BASE_TRAVADA
    except Exception:  # noqa: BLE001 - nenhuma falha de base derruba a tool
        logger.exception("falha ao consultar a base")
        return [], AVISO_BASE_TRAVADA


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

    linhas, aviso = _ler(
        caminho_db,
        lambda c: buscar_por_tema(c, tema, limite=limite, apenas_vigentes=apenas_vigentes),
    )
    return RespostaConsulta(
        tema=tema,
        total=len(linhas),
        resultados=[_para_modelo(linha, tema) for linha in linhas],
        aviso=aviso or AVISO_FONTE,
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

    linhas, aviso = _ler(caminho_db, lambda c: publicadas_no_periodo(c, dias=dias, limite=limite))

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
