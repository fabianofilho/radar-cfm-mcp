"""As duas tools: consultar por tema e monitorar novas resoluções."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from radar_cfm_mcp.extract.vigencia import detectar_suspensao
from radar_cfm_mcp.store.db import BaseIndisponivel, conectar
from radar_cfm_mcp.store.queries import (
    anotar_revogacao,
    buscar_por_identificador,
    buscar_por_tema,
    contar_no_periodo,
    contar_por_tema,
    identificador_no_tema,
    publicadas_no_periodo,
    sem_data_publicacao,
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
    "a ementa. Sem a opção de texto integral, o sync baixa o PDF apenas das resoluções "
    "cuja ementa toca em IA ou telemedicina. O conteúdo destes artigos não foi lido: "
    "abra a URL antes de afirmar o que a norma diz."
)
AVISO_REVOGACAO_QUEBRADA = (
    "Há resultado cujo 'revogada_por' aponta para uma resolução que não existe: o portal "
    "do CFM erra o ano nesses casos. Quando dá para identificar pelo número, a candidata "
    "vem em 'revogada_por_provavel', que é inferência nossa. Confirme na URL de origem."
)
AVISO_POR_NUMERO = (
    "O tema foi lido como o número de uma resolução, então a busca foi direta e "
    "'apenas_vigentes' não se aplica: quem pede uma norma pelo número precisa vê-la "
    "mesmo revogada, e a revogação vem marcada no resultado."
)
AVISO_BUSCA_POR_PALAVRA = (
    "A busca casa qualquer palavra do tema, não a frase inteira, então um tema com "
    "várias palavras infla o total com resoluções que só têm uma delas. Trate o total "
    "como 'quantas mencionam alguma dessas palavras', não como 'quantas tratam disso'."
)


AVISO_SUSPENSA = (
    "Atenção: há resultado com suspensão marcada na ementa (campo 'suspensa'). O portal "
    "do CFM só estrutura revogação, então essas normas vêm com vigente=true. Para quem "
    "vai aplicar, suspensa tem o mesmo efeito de revogada: confirme na URL oficial."
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
    vigente: bool = Field(
        description=(
            "Só reflete revogação, que é o que o portal marca em campo próprio. "
            "Uma norma suspensa vem com vigente=true: veja 'suspensa' antes de "
            "concluir que ela está produzindo efeito."
        )
    )
    suspensa: bool = Field(
        default=False,
        description=(
            "A ementa traz marcação de suspensão. Para quem vai aplicar a norma, "
            "suspensa tem o mesmo efeito prático de revogada."
        ),
    )
    suspensao_parcial: bool = Field(
        default=False,
        description="Só alguns dispositivos foram suspensos; o resto da norma segue valendo",
    )
    nota_vigencia: str | None = Field(
        default=None, description="O texto da marcação, como o CFM escreveu"
    )
    revogada_por: str | None = Field(
        default=None,
        description=(
            "Identificador da resolução que revogou esta, como o portal do CFM publica. "
            "Confira 'revogada_por_confere' antes de citar: o portal erra o ano em "
            "alguns casos."
        ),
    )
    revogada_por_confere: bool | None = Field(
        default=None,
        description=(
            "False quando o identificador acima não existe na base, ou seja, o portal "
            "publicou um ano que não bate. Nesse caso veja 'revogada_por_provavel'."
        ),
    )
    revogada_por_provavel: str | None = Field(
        default=None,
        description=(
            "Única resolução com aquele número, quando o identificador publicado não "
            "existe. É inferência nossa a partir do número, não o que o CFM publicou: "
            "confirme na URL de origem antes de citar."
        ),
    )
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
    total: int = Field(
        description=(
            "Quantas resoluções datadas no período casam com o filtro, na base inteira, "
            "não quantas vieram nesta resposta."
        )
    )
    retornados: int = Field(
        default=0, description="Quantas vieram em 'resultados', no máximo 'limite'"
    )
    truncado: bool = Field(
        default=False,
        description=(
            "True quando total > retornados. As que vieram são as mais recentes; as "
            "demais existem e não estão aqui. Aumente 'limite' ou encurte 'dias'."
        ),
    )
    sem_data_publicacao: int = Field(
        default=0,
        description=(
            "Quantas resoluções da base não têm data e por isso ficam fora deste "
            "filtro, existindo ou não no período. Número alto com total=0 significa "
            "'não sei', não 'nada foi publicado'."
        ),
    )
    resultados: list[ResolucaoCFM]
    aviso: str | None = None


def _radicais(termo: str) -> list[str]:
    """Palavras do termo que servem de âncora, já cortadas na desinência.

    Só palavras de quatro letras ou mais: "de" e "em" casariam em qualquer lugar.
    O corte tolera gênero e número ("assíncrono" onde o texto diz "assíncrona"),
    mas para no radical de cinco letras, senão "tele" casaria com telefone.
    """
    palavras = {p for p in re.split(r"\W+", termo.lower()) if len(p) >= 4}
    return [p[:-2] if len(p) >= 7 else p for p in palavras]


def _trecho(texto: str | None, termo: str, *, janela: int = 260) -> str | None:
    """Pedaço do texto em volta do ponto que melhor cobre o termo, ou None.

    Devolver o começo do documento quando o termo não aparece seria pior que
    devolver nada: o campo se chama ``trecho_relevante`` e quem lê trata como
    resposta à pergunta feita. A abertura de uma resolução é sempre plausível,
    então o erro passa despercebido e vira citação errada.

    **Escolhe a janela que reúne mais palavras do termo, não a primeira
    ocorrência de uma delas.** Para "registro em prontuário telemedicina", a
    primeira ocorrência de "telemedicina" costuma ser o cabeçalho da resolução,
    que não responde nada; o trecho útil é onde as três palavras aparecem juntas.
    """
    if not texto:
        return None

    alvo = texto.lower()
    posicao = alvo.find(termo.strip().lower())

    if posicao < 0:
        radicais = _radicais(termo)
        if not radicais:
            return None

        ocorrencias: list[int] = []
        for radical in radicais:
            inicio_busca = 0
            while (achado := alvo.find(radical, inicio_busca)) >= 0:
                ocorrencias.append(achado)
                inicio_busca = achado + 1
                if len(ocorrencias) > 400:  # texto enorme: já há candidatos de sobra
                    break
        if not ocorrencias:
            return None

        def cobertura(centro: int) -> tuple[int, int]:
            perto = alvo[max(0, centro - janela // 2) : centro + janela // 2]
            # Empate resolvido pela posição, para o resultado não variar entre chamadas.
            return sum(radical in perto for radical in radicais), -centro

        posicao = max(ocorrencias, key=cobertura)

    inicio = max(0, posicao - janela // 2)
    fim = min(len(texto), posicao + janela // 2)
    prefixo = "..." if inicio > 0 else ""
    sufixo = "..." if fim < len(texto) else ""
    return f"{prefixo}{texto[inicio:fim].strip()}{sufixo}"


def _para_modelo(linha: dict[str, Any], termo: str | None = None) -> ResolucaoCFM:
    texto = linha.get("texto_completo")
    # Lido da ementa a cada resposta, e nao de coluna: assim vale na base que ja
    # existe, sem esperar a proxima coleta.
    suspensao = detectar_suspensao(linha.get("ementa"))
    return ResolucaoCFM(
        identificador=linha["identificador"],
        numero=linha["numero"],
        ano=linha["ano"],
        data_publicacao=linha.get("data_publicacao"),
        ementa=linha.get("ementa") or None,
        trecho_relevante=_trecho(texto, termo) if termo else None,
        vigente=bool(linha.get("vigente", True)),
        revogada_por_confere=linha.get("revogada_por_confere"),
        revogada_por_provavel=linha.get("revogada_por_provavel"),
        suspensa=suspensao.suspensa,
        suspensao_parcial=suspensao.parcial,
        nota_vigencia=suspensao.nota,
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

    # "2314/2022" e endereço, não assunto: o índice de texto não indexa número
    # solto e devolvia zero com a resolução na base.
    endereco = identificador_no_tema(tema)

    def consultar(c: duckdb.DuckDBPyConnection) -> tuple[list[dict[str, Any]], int]:
        if endereco is not None:
            numero, ano = endereco
            achadas = buscar_por_identificador(c, numero, ano)
            if achadas:
                return anotar_revogacao(c, achadas), len(achadas)
        return (
            anotar_revogacao(
                c, buscar_por_tema(c, tema, limite=limite, apenas_vigentes=apenas_vigentes)
            ),
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
    # Vale sempre que o tema tem mais de uma palavra, nao so quando trunca: um
    # termo sem sentido trouxe 2 resultados porque o texto deles tinha uma das
    # palavras, e ali nao havia truncamento para disparar o aviso.
    if endereco is None and len(tema.split()) > 1:
        avisos.append(AVISO_BUSCA_POR_PALAVRA)
    if linhas and not any(linha.get("texto_completo") for linha in linhas):
        avisos.append(AVISO_SO_EMENTA)
    resultados = [_para_modelo(linha, tema) for linha in linhas]
    if any(r.suspensa for r in resultados):
        avisos.append(AVISO_SUSPENSA)
    if endereco is not None and resultados:
        avisos.append(AVISO_POR_NUMERO)
    if any(r.revogada_por_confere is False for r in resultados):
        avisos.append(AVISO_REVOGACAO_QUEBRADA)
    return RespostaConsulta(
        tema=tema,
        total=total,
        retornados=len(linhas),
        truncado=truncado,
        resultados=resultados,
        aviso=" ".join(avisos),
    )


async def monitorar_novas_resolucoes(
    dias: int = 30,
    *,
    caminho_db: str,
    palavras_chave: tuple[str, ...] = (),
    limite: int = 50,
) -> RespostaMonitoramento:
    """Resoluções publicadas no período, opcionalmente só as que citam as palavras-chave."""
    if dias <= 0:
        return RespostaMonitoramento(
            dias=dias,
            total=0,
            resultados=[],
            aviso="O parâmetro 'dias' precisa ser maior que zero.",
        )

    palavras = tuple(p for p in palavras_chave if p.strip())

    def consultar(
        c: duckdb.DuckDBPyConnection,
    ) -> tuple[list[dict[str, Any]], int, tuple[int, int]]:
        linhas = publicadas_no_periodo(c, dias=dias, limite=limite, palavras_chave=palavras)
        return (
            anotar_revogacao(c, linhas),
            contar_no_periodo(c, dias=dias, palavras_chave=palavras),
            sem_data_publicacao(c),
        )

    vazio: tuple[list[dict[str, Any]], int, tuple[int, int]] = ([], 0, (0, 0))
    (linhas, total, (sem_data, total_base)), aviso = _ler(caminho_db, consultar, padrao=vazio)

    truncado = total > len(linhas)
    avisos = [aviso or AVISO_FONTE]
    if truncado:
        avisos.append(
            f"Casaram {total} resoluções no período e estão aqui as {len(linhas)} mais "
            f"recentes. Aumente 'limite' ou encurte 'dias' para ver as demais."
        )
    if sem_data and total_base:
        avisos.append(
            f"{sem_data} das {total_base} resoluções da base estão sem data de publicação "
            f"e não entram neste filtro, existindo ou não no período. A data é extraída do "
            f"texto do PDF, que nem toda resolução antiga traz. Resultado vazio aqui não "
            f"significa que nada foi publicado: confirme pela busca por tema."
        )
    return RespostaMonitoramento(
        dias=dias,
        total=total,
        retornados=len(linhas),
        truncado=truncado,
        sem_data_publicacao=sem_data,
        resultados=[_para_modelo(linha) for linha in linhas],
        aviso=" ".join(avisos),
    )
