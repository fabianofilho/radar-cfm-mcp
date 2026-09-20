"""Fixtures compartilhadas."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from radar_cfm_mcp.store.db import aplicar_schema

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def html_busca() -> str:
    """Recorte real da página de busca do CFM (capturado em 2026-09-20)."""
    return (FIXTURES / "busca_cfm.html").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    conexao = duckdb.connect(str(tmp_path / "teste.duckdb"))
    aplicar_schema(conexao)
    yield conexao
    conexao.close()


@pytest.fixture
def caminho_db(tmp_path: Path) -> str:
    return str(tmp_path / "tools.duckdb")
