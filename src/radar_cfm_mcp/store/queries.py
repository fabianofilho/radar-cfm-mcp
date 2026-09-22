"""Queries sobre a base de resoluções."""

from __future__ import annotations

import logging
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


def gravar(conexao: duckdb.DuckDBPyConnection, registros: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert em massa. Devolve (novos, atualizados)."""
    if not registros:
        return 0, 0

    unicos = {r["identificador"]: r for r in registros}
    linhas = list(unicos.values())
    colunas = list(linhas[0].keys())
    lista = ", ".join(colunas)
    marcadores = ", ".join("?" for _ in colunas)
    atribuicoes = ", ".join(f"{c} = excluded.{c}" for c in colunas if c != "identificador")

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
