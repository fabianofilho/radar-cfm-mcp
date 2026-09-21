"""DuckDB com full-text search sobre ementa e texto completo."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSAO = 1

_DDL = """
CREATE TABLE IF NOT EXISTS resolucoes (
    identificador          VARCHAR PRIMARY KEY,
    numero                 VARCHAR NOT NULL,
    ano                    VARCHAR NOT NULL,
    data_publicacao        DATE,
    ementa                 VARCHAR,
    texto_completo         VARCHAR,
    vigente                BOOLEAN NOT NULL DEFAULT TRUE,
    revogada_por           VARCHAR,
    url_origem             VARCHAR NOT NULL,
    url_pdf                VARCHAR NOT NULL,
    metadados_incompletos  BOOLEAN NOT NULL DEFAULT FALSE,
    data_coleta            TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS schema_meta (
    chave VARCHAR PRIMARY KEY,
    valor VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_res_data ON resolucoes (data_publicacao);
CREATE INDEX IF NOT EXISTS idx_res_vigente ON resolucoes (vigente);
"""


class BaseIndisponivel(RuntimeError):
    """A base existe mas está travada, tipicamente um sync em curso."""


def _instalar_fts(conexao: duckdb.DuckDBPyConnection) -> bool:
    """Instala a extensão FTS. Devolve False se não estiver disponível offline."""
    try:
        conexao.execute("INSTALL fts")
        conexao.execute("LOAD fts")
        return True
    except duckdb.Error as erro:
        logger.warning("FTS indisponível (%s); a busca cai para LIKE", erro)
        return False


def reindexar_fts(conexao: duckdb.DuckDBPyConnection) -> bool:
    """(Re)cria o índice FTS sobre ementa e texto completo.

    O índice precisa ser recriado depois de cada carga: o PRAGMA do DuckDB monta
    um snapshot, não um índice incremental.
    """
    if not _instalar_fts(conexao):
        return False
    try:
        conexao.execute(
            "PRAGMA create_fts_index('resolucoes', 'identificador', 'ementa', "
            "'texto_completo', overwrite=1)"
        )
        return True
    except duckdb.Error as erro:
        logger.warning("não consegui criar o índice FTS: %s", erro)
        return False


def aplicar_schema(conexao: duckdb.DuckDBPyConnection) -> None:
    """Cria tabelas e índices. Idempotente."""
    conexao.execute(_DDL)
    conexao.execute(
        "INSERT INTO schema_meta VALUES ('versao', ?) "
        "ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor",
        [str(SCHEMA_VERSAO)],
    )


@contextmanager
def conectar(
    caminho: Path | str,
    *,
    somente_leitura: bool = False,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre o DuckDB. Em modo leitura não cria nada nem aplica schema.

    Raises:
        FileNotFoundError: em leitura, quando a base ainda não existe.
        BaseIndisponivel: quando o arquivo está travado por outro processo.
    """
    em_memoria = str(caminho) == ":memory:"
    if somente_leitura and not em_memoria and not Path(caminho).exists():
        raise FileNotFoundError(f"base local ainda não existe em {caminho}")
    if not somente_leitura and not em_memoria:
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)

    try:
        conexao = duckdb.connect(str(caminho), read_only=somente_leitura and not em_memoria)
    except duckdb.IOException as erro:
        raise BaseIndisponivel(str(erro)) from erro

    try:
        if not somente_leitura:
            aplicar_schema(conexao)
        else:
            _instalar_fts(conexao)
        yield conexao
    finally:
        conexao.close()
