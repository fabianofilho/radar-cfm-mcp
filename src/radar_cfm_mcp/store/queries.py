"""Queries sobre a base de resoluções."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_COLUNAS = (
    "identificador, numero, ano, data_publicacao, ementa, texto_completo, "
    "vigente, revogada_por, url_origem, url_pdf, metadados_incompletos"
)


def _para_dicts(resultado: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    colunas = [d[0] for d in resultado.description or []]
    return [dict(zip(colunas, linha, strict=True)) for linha in resultado.fetchall()]


def buscar_por_tema(
    conexao: duckdb.DuckDBPyConnection,
    tema: str,
    *,
    limite: int = 10,
    apenas_vigentes: bool = True,
) -> list[dict[str, Any]]:
    """Busca por tema, do mais relevante para o mais recente.

    Usa o índice FTS quando ele existe; se não existir (extensão indisponível
    offline, ou base ainda sem índice), cai para LIKE. A ordem final é sempre
    relevância e depois data decrescente.
    """
    filtro_vigente = "AND vigente" if apenas_vigentes else ""
    try:
        return _para_dicts(
            conexao.execute(
                f"""
                SELECT {_COLUNAS}, fts_main_resolucoes.match_bm25(identificador, ?) AS relevancia
                FROM resolucoes
                WHERE relevancia IS NOT NULL {filtro_vigente}
                ORDER BY relevancia DESC, data_publicacao DESC
                LIMIT ?
                """,
                [tema, limite],
            )
        )
    except duckdb.Error as erro:
        logger.debug("FTS indisponível na consulta (%s); usando LIKE", erro)

    padrao = f"%{tema.strip()}%"
    return _para_dicts(
        conexao.execute(
            f"""
            SELECT {_COLUNAS}, NULL AS relevancia
            FROM resolucoes
            WHERE (lower(coalesce(ementa, '')) LIKE lower(?)
                   OR lower(coalesce(texto_completo, '')) LIKE lower(?))
              {filtro_vigente}
            ORDER BY data_publicacao DESC, identificador
            LIMIT ?
            """,
            [padrao, padrao, limite],
        )
    )


# "2314", "2314/2022", "2.314/2022", "Resolucao CFM n 2.314/2022". O ponto de
# milhar e opcional porque o CFM escreve dos dois jeitos no proprio texto.
_IDENTIFICADOR = re.compile(
    r"^(?:resolu[çc][ãa]o\s+)?(?:cfm\s+)?(?:n[º°o]?\.?\s*)?"
    r"(\d{1,2}[.\s]?\d{3}|\d{1,4})\s*(?:/\s*(\d{4}))?$",
    re.I,
)


def identificador_no_tema(tema: str) -> tuple[str, str | None] | None:
    """Número e ano, quando o tema é o endereço de uma resolução e não um assunto.

    Quem digita "2314/2022" quer aquela norma, não uma busca textual. O índice
    de texto não serve para isso: ele não indexa número solto, então a consulta
    voltava zero mesmo com a resolução na base.
    """
    limpo = " ".join(tema.split())
    achado = _IDENTIFICADOR.match(limpo)
    if not achado:
        return None
    numero = achado.group(1).replace(".", "").replace(" ", "")
    return numero, achado.group(2)


def buscar_por_identificador(
    conexao: duckdb.DuckDBPyConnection,
    numero: str,
    ano: str | None = None,
) -> list[dict[str, Any]]:
    """Resolução pelo número, com o ano quando informado.

    Não filtra por vigência de propósito: quem pede uma norma pelo número quer
    ver aquela norma, inclusive para descobrir que foi revogada.
    """
    if ano:
        return _para_dicts(
            conexao.execute(
                f"SELECT {_COLUNAS}, NULL AS relevancia FROM resolucoes "
                "WHERE numero = ? AND ano = ?",
                [numero, ano],
            )
        )
    return _para_dicts(
        conexao.execute(
            f"SELECT {_COLUNAS}, NULL AS relevancia FROM resolucoes "
            "WHERE numero = ? ORDER BY ano DESC",
            [numero],
        )
    )


def anotar_revogacao(
    conexao: duckdb.DuckDBPyConnection,
    linhas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Diz se o alvo da revogação existe, e qual seria o provável quando não.

    O portal do CFM erra o ano em ``ANO_REVOGADA``: a Resolução 1643/2002 vem
    apontando para "2314/2024", e a 2314 é de 2022. Auditado contra a fonte em
    22/09/2026, nos seis casos da base o portal entrega exatamente isso, então é
    erro da fonte e não da coleta.

    O valor de ``revogada_por`` fica como veio, porque é o que o órgão publica.
    Mas repassar um identificador que não existe faz quem consulta procurar uma
    norma inexistente, e a revogação é justamente o que não pode passar batido.
    Quando há uma única resolução com aquele número, ela vai em
    ``revogada_por_provavel``, marcada como inferência nossa.
    """
    alvos = {str(linha["revogada_por"]) for linha in linhas if linha.get("revogada_por")}
    if not alvos:
        return linhas

    marcadores = ", ".join("?" for _ in alvos)
    existentes = {
        linha[0]
        for linha in conexao.execute(
            f"SELECT identificador FROM resolucoes WHERE identificador IN ({marcadores})",
            list(alvos),
        ).fetchall()
    }

    provaveis: dict[str, str] = {}
    for alvo in alvos - existentes:
        numero = alvo.split("/")[0]
        candidatos = conexao.execute(
            "SELECT identificador FROM resolucoes WHERE numero = ?", [numero]
        ).fetchall()
        # Mais de um ano com o mesmo numero nao permite escolher sem chutar.
        if len(candidatos) == 1:
            provaveis[alvo] = candidatos[0][0]

    for linha in linhas:
        publicado = linha.get("revogada_por")
        if not publicado:
            continue
        publicado = str(publicado)
        linha["revogada_por_confere"] = publicado in existentes
        linha["revogada_por_provavel"] = provaveis.get(publicado)
    return linhas


def contar_por_tema(
    conexao: duckdb.DuckDBPyConnection,
    tema: str,
    *,
    apenas_vigentes: bool = True,
) -> int:
    """Quantas resoluções casam com o tema, ignorando o limite de exibição.

    Sem isto, a resposta diz "total: 10" tanto para um tema com 10 resoluções
    quanto para um com 80, e quem lê conclui que viu tudo que existe.

    Repete a mesma lógica da busca, FTS com queda para LIKE, porque contar por um
    critério e listar por outro daria um total que não descreve a lista.
    """
    filtro_vigente = "AND vigente" if apenas_vigentes else ""
    try:
        linha = conexao.execute(
            f"""
            SELECT count(*) FROM (
                SELECT fts_main_resolucoes.match_bm25(identificador, ?) AS relevancia, vigente
                FROM resolucoes
            ) WHERE relevancia IS NOT NULL {filtro_vigente}
            """,
            [tema],
        ).fetchone()
        return int(linha[0]) if linha else 0
    except duckdb.Error as erro:
        logger.debug("FTS indisponível na contagem (%s); usando LIKE", erro)

    padrao = f"%{tema.strip()}%"
    linha = conexao.execute(
        f"""
        SELECT count(*) FROM resolucoes
        WHERE (lower(coalesce(ementa, '')) LIKE lower(?)
               OR lower(coalesce(texto_completo, '')) LIKE lower(?))
          {filtro_vigente}
        """,
        [padrao, padrao],
    ).fetchone()
    return int(linha[0]) if linha else 0


def sem_data_publicacao(conexao: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Quantas resoluções estão sem data de publicação, e o total.

    O monitor filtra por data. Sem este número, uma base em que a extração de
    data falhou devolve "nenhuma resolução nova" com a mesma cara de uma semana
    tranquila, e o falso negativo passa despercebido justamente em vigilância
    regulatória, onde ele é caro.
    """
    linha = conexao.execute(
        "SELECT count(*) FILTER (WHERE data_publicacao IS NULL), count(*) FROM resolucoes"
    ).fetchone()
    return (int(linha[0]), int(linha[1])) if linha else (0, 0)


def publicadas_no_periodo(
    conexao: duckdb.DuckDBPyConnection,
    *,
    dias: int,
    limite: int = 50,
) -> list[dict[str, Any]]:
    """Resoluções publicadas nos últimos ``dias``."""
    corte = date.today() - timedelta(days=dias)
    return _para_dicts(
        conexao.execute(
            f"""
            SELECT {_COLUNAS}
            FROM resolucoes
            WHERE data_publicacao >= ?
            ORDER BY data_publicacao DESC, identificador
            LIMIT ?
            """,
            [corte, limite],
        )
    )


# Colunas que custam caro para obter e que uma coleta pode simplesmente não
# trazer. Para elas, o upsert só substitui quando o novo valor tem conteúdo.
_PRESERVAR_SE_VAZIO = ("texto_completo",)


def _atribuicao(coluna: str) -> str:
    """Como o upsert atualiza esta coluna.

    O sync baixa o PDF só de parte das resoluções, então a coleta seguinte traz
    ``texto_completo = NULL`` para todas as outras. Com a atribuição direta, a
    coleta da madrugada apagaria todo texto já baixado, e o prejuízo só
    apareceria quando alguém consultasse e recebesse "trecho não encontrado".

    Texto ausente na coleta significa "não busquei desta vez", nunca "a norma
    ficou sem texto".
    """
    if coluna in _PRESERVAR_SE_VAZIO:
        return f"{coluna} = coalesce(nullif(excluded.{coluna}, ''), resolucoes.{coluna})"
    return f"{coluna} = excluded.{coluna}"


def gravar(conexao: duckdb.DuckDBPyConnection, registros: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert em massa. Devolve (novos, atualizados)."""
    if not registros:
        return 0, 0

    unicos = {r["identificador"]: r for r in registros}
    linhas = list(unicos.values())
    colunas = list(linhas[0].keys())
    lista = ", ".join(colunas)
    marcadores = ", ".join("?" for _ in colunas)
    atribuicoes = ", ".join(_atribuicao(coluna) for coluna in colunas if coluna != "identificador")

    staging = "staging_resolucoes"
    conexao.execute(
        f"CREATE OR REPLACE TEMP TABLE {staging} AS SELECT {lista} FROM resolucoes LIMIT 0"
    )
    conexao.executemany(
        f"INSERT INTO {staging} ({lista}) VALUES ({marcadores})",
        [[r[c] for c in colunas] for r in linhas],
    )
    contagem = conexao.execute(
        f"""
        SELECT count(*) FROM {staging} s
        WHERE NOT EXISTS (
            SELECT 1 FROM resolucoes r WHERE r.identificador = s.identificador
        )
        """
    ).fetchone()
    novos = int(contagem[0]) if contagem else 0

    conexao.execute(
        f"""
        INSERT INTO resolucoes ({lista})
        SELECT {lista} FROM {staging}
        ON CONFLICT (identificador) DO UPDATE SET {atribuicoes}, data_coleta = now()
        """
    )
    conexao.execute(f"DROP TABLE {staging}")
    return novos, len(linhas) - novos


def sem_texto_completo(
    conexao: duckdb.DuckDBPyConnection,
    *,
    limite: int = 50,
) -> list[dict[str, Any]]:
    """Resoluções cujo PDF ainda não foi baixado."""
    return _para_dicts(
        conexao.execute(
            f"""
            SELECT {_COLUNAS}
            FROM resolucoes
            WHERE (texto_completo IS NULL OR texto_completo = '')
              AND NOT metadados_incompletos
            ORDER BY data_publicacao DESC, identificador
            LIMIT ?
            """,
            [limite],
        )
    )
