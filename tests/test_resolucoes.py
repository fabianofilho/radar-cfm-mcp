"""Tools: consulta por tema, monitoramento e vigência."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import duckdb

from radar_cfm_mcp.mcp_server.tools.resolucoes import (
    consultar_resolucao_cfm,
    monitorar_novas_resolucoes,
)
from radar_cfm_mcp.store.db import conectar, reindexar_fts
from radar_cfm_mcp.store.queries import buscar_por_tema, gravar, publicadas_no_periodo


def _registro(**campos: Any) -> dict[str, Any]:
    base = {
        "identificador": "1/2020",
        "numero": "1",
        "ano": "2020",
        "data_publicacao": date.today() - timedelta(days=10),
        "ementa": "",
        "texto_completo": None,
        "vigente": True,
        "revogada_por": None,
        "url_origem": "https://sistemas.cfm.org.br/normas/visualizar/resolucoes/BR/2020/1",
        "url_pdf": "https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2020/1_2020.pdf",
        "metadados_incompletos": False,
    }
    base.update(campos)
    return base


def test_gravar_e_idempotente(db: duckdb.DuckDBPyConnection) -> None:
    linha = _registro(ementa="Dispõe sobre telemedicina")
    assert gravar(db, [linha]) == (1, 0)
    assert gravar(db, [linha]) == (0, 1)
    assert db.execute("SELECT count(*) FROM resolucoes").fetchone()[0] == 1


def test_gravar_deduplica_no_mesmo_lote(db: duckdb.DuckDBPyConnection) -> None:
    linha = _registro(ementa="a")
    novos, _ = gravar(db, [linha, {**linha, "ementa": "b"}])
    assert novos == 1
    assert db.execute("SELECT ementa FROM resolucoes").fetchone()[0] == "b"


def test_busca_por_tema_acha_na_ementa(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            _registro(identificador="1/2020", ementa="Dispõe sobre telemedicina"),
            _registro(
                identificador="2/2021", numero="2", ano="2021", ementa="Dispõe sobre publicidade"
            ),
        ],
    )
    achados = buscar_por_tema(db, "telemedicina")
    assert [a["identificador"] for a in achados] == ["1/2020"]


def test_busca_omite_revogadas_por_padrao(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            _registro(
                identificador="1/2020", ementa="telemedicina", vigente=False, revogada_por="9/2024"
            ),
            _registro(identificador="2/2021", numero="2", ano="2021", ementa="telemedicina"),
        ],
    )
    assert [a["identificador"] for a in buscar_por_tema(db, "telemedicina")] == ["2/2021"]
    assert len(buscar_por_tema(db, "telemedicina", apenas_vigentes=False)) == 2


def test_periodo_exclui_antigas(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            _registro(identificador="1/2020", data_publicacao=date.today() - timedelta(days=5)),
            _registro(
                identificador="2/2019",
                numero="2",
                ano="2019",
                data_publicacao=date.today() - timedelta(days=400),
            ),
        ],
    )
    assert [a["identificador"] for a in publicadas_no_periodo(db, dias=30)] == ["1/2020"]


def test_fts_indexa_e_a_busca_continua_funcionando(db: duckdb.DuckDBPyConnection) -> None:
    """Se o FTS não estiver disponível, a busca cai para LIKE — mas responde."""
    gravar(db, [_registro(ementa="Dispõe sobre inteligência artificial em medicina")])
    reindexar_fts(db)  # pode falhar offline; a busca precisa funcionar de qualquer jeito
    assert len(buscar_por_tema(db, "inteligência artificial")) == 1


async def test_base_inexistente_avisa_em_vez_de_quebrar(caminho_db: str) -> None:
    resposta = await consultar_resolucao_cfm("telemedicina", caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None and "sincronizada" in resposta.aviso


async def test_consulta_traz_fonte_e_trecho(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            [
                _registro(
                    ementa="Dispõe sobre telemedicina",
                    texto_completo=(
                        "Artigo 1. A telemedicina é o exercício da medicina "
                        "mediado por tecnologias."
                    ),
                )
            ],
        )
    resposta = await consultar_resolucao_cfm("telemedicina", caminho_db=caminho_db)
    assert resposta.total == 1
    resultado = resposta.resultados[0]
    assert resultado.url_origem.startswith("https://")
    assert resultado.trecho_relevante is not None
    assert "telemedicina" in resultado.trecho_relevante.lower()
    assert resposta.aviso is not None and "confira o texto oficial" in resposta.aviso


async def test_revogada_vem_marcada(caminho_db: str) -> None:
    """Sinalizar revogação é requisito: o usuário não pode citar norma revogada."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_registro(ementa="telemedicina", vigente=False, revogada_por="9/2024")])
    resposta = await consultar_resolucao_cfm(
        "telemedicina", caminho_db=caminho_db, apenas_vigentes=False
    )
    assert resposta.resultados[0].vigente is False
    assert resposta.resultados[0].revogada_por == "9/2024"


async def test_monitorar_filtra_por_tema(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            [
                _registro(identificador="1/2026", ementa="Dispõe sobre telemedicina"),
                _registro(identificador="2/2026", numero="2", ementa="Dispõe sobre honorários"),
            ],
        )
    resposta = await monitorar_novas_resolucoes(
        90, caminho_db=caminho_db, palavras_chave=("telemedicina",)
    )
    assert [r.identificador for r in resposta.resultados] == ["1/2026"]


async def test_dias_invalido(caminho_db: str) -> None:
    resposta = await monitorar_novas_resolucoes(0, caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None
