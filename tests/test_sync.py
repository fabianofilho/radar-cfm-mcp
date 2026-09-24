"""Sync: o que ja tem texto nao volta ao parser, e a data e refeita do texto."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from radar_cfm_mcp.crawler import sync as modulo_sync
from radar_cfm_mcp.crawler.search import Resolucao
from radar_cfm_mcp.crawler.sync import reextrair_datas, sincronizar
from radar_cfm_mcp.extract.parser import TextoExtraido
from radar_cfm_mcp.store.queries import gravar


def _resolucao(numero: str, ano: str = "2026", ementa: str = "Homologa eleição") -> Resolucao:
    return Resolucao(
        numero=numero,
        ano=ano,
        data_no_portal=None,
        ementa=ementa,
        vigente=True,
        revogada_por=None,
        url_origem=f"https://sistemas.cfm.org.br/normas/visualizar/resolucoes/BR/{ano}/{numero}",
        url_pdf=f"https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/{ano}/{numero}_{ano}.pdf",
    )


def _linha(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identificador": "1/2026",
        "numero": "1",
        "ano": "2026",
        "data_publicacao": None,
        "ementa": "",
        "texto_completo": None,
        "vigente": True,
        "revogada_por": None,
        "url_origem": "https://sistemas.cfm.org.br/normas/visualizar/resolucoes/BR/2026/1",
        "url_pdf": "https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2026/1_2026.pdf",
        "metadados_incompletos": False,
    }
    base.update(campos)
    return base


class _CrawlerFalso:
    """Faz o papel do portal: devolve a lista e conta os PDFs pedidos."""

    def __init__(self, resolucoes: list[Resolucao]) -> None:
        self.resolucoes = resolucoes
        self.pdfs_pedidos: list[str] = []
        self.extraidos: list[bytes] = []

    def __call__(self, **_: Any) -> _CrawlerFalso:
        return self

    async def __aenter__(self) -> _CrawlerFalso:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def varrer(self, max_paginas: int | None = None) -> list[Resolucao]:
        return self.resolucoes

    async def baixar_pdf(self, resolucao: Resolucao) -> bytes:
        self.pdfs_pedidos.append(resolucao.identificador)
        return f"pdf de {resolucao.identificador}".encode()


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> _CrawlerFalso:
    falso = _CrawlerFalso([])
    monkeypatch.setattr(modulo_sync, "CrawlerCFM", falso)

    def extrair(conteudo: bytes) -> TextoExtraido:
        falso.extraidos.append(conteudo)
        identificador = conteudo.decode().removeprefix("pdf de ")
        texto = f"RESOLUÇÃO CFM Nº {identificador}\n(Publicada no D.O.U. de 15 Jan 2026)\nTexto."
        return TextoExtraido(texto=texto, paginas=1, metadados_incompletos=False)

    monkeypatch.setattr(modulo_sync, "extrair_pdf", extrair)
    return falso


async def test_texto_integral_baixa_a_nova_e_ela_entra_com_texto_e_data(
    db: duckdb.DuckDBPyConnection, portal: _CrawlerFalso, tmp_path: Path
) -> None:
    """CFM-3: resolução nova fora do tema ficava sem texto e sem data."""
    portal.resolucoes = [_resolucao("2472")]

    resultado = await sincronizar(
        db,
        palavras_chave=("telemedicina",),
        delay_segundos=0.5,
        diretorio_cache=tmp_path / "pdfs",
        texto_integral=True,
    )

    assert resultado.pdfs_baixados == 1
    texto, data = db.execute(
        "SELECT texto_completo, data_publicacao FROM resolucoes WHERE identificador = '2472/2026'"
    ).fetchone()
    assert texto and "2472/2026" in texto
    assert data == date(2026, 1, 15)


async def test_resolucao_com_texto_nao_volta_ao_parser_e_mantem_a_marca(
    db: duckdb.DuckDBPyConnection, portal: _CrawlerFalso, tmp_path: Path
) -> None:
    """O sync diário com texto integral não relê as 2.457 do cache toda noite."""
    gravar(
        db,
        [
            _linha(
                identificador="10/2026",
                numero="10",
                texto_completo="(Publicada no D.O.U. de 3 de março de 2026) texto antigo",
                metadados_incompletos=True,
            )
        ],
    )
    portal.resolucoes = [_resolucao("10")]

    resultado = await sincronizar(
        db,
        palavras_chave=(),
        delay_segundos=0.5,
        diretorio_cache=tmp_path / "pdfs",
        texto_integral=True,
    )

    assert resultado.pdfs_baixados == 0
    assert portal.pdfs_pedidos == []
    assert portal.extraidos == []
    texto, incompleto, data = db.execute(
        "SELECT texto_completo, metadados_incompletos, data_publicacao FROM resolucoes"
    ).fetchone()
    assert texto.endswith("texto antigo")
    assert incompleto is True
    # A data que faltava sai do texto já gravado, no passo de reextração.
    assert data == date(2026, 3, 3)


def test_reextrair_preenche_corrige_e_nao_apaga(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            # sem data, mas o texto diz
            _linha(
                identificador="2373/2023",
                numero="2373",
                ano="2023",
                texto_completo="RESOLUÇÃO CFM Nº 2.373/2023\nPublicada no D.O.U. de 15 Jan 2024",
            ),
            # data errada: veio de outra norma citada no cabeçalho
            _linha(
                identificador="2109/2014",
                numero="2109",
                ano="2014",
                data_publicacao=date(2013, 8, 28),
                texto_completo=(
                    "RESOLUÇÃO CFM Nº 2.109/2014\n(Publicada no D.O.U. de 31 out. 2014, "
                    "Seção I, p. 288)\n(Resolução CFM nº 2.023/2013, publicada no\n"
                    "D.O.U. de 28 de agosto de 2013, Seção I, p. 83-85)"
                ),
            ),
            # data certa e texto sem data: fica como está
            _linha(
                identificador="5/2020",
                numero="5",
                ano="2020",
                data_publicacao=date(2020, 5, 5),
                texto_completo="texto sem cabeçalho de publicação",
            ),
        ],
    )

    assert reextrair_datas(db) == (1, 1)
    datas = dict(db.execute("SELECT identificador, data_publicacao FROM resolucoes").fetchall())
    assert datas == {
        "2373/2023": date(2024, 1, 15),
        "2109/2014": date(2014, 10, 31),
        "5/2020": date(2020, 5, 5),
    }
