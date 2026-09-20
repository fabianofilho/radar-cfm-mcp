"""Crawler: parsing do JSON embutido, paginação e delay."""

from __future__ import annotations

import httpx
import pytest
import respx

from radar_cfm_mcp.crawler.cache import CachePdf
from radar_cfm_mcp.crawler.search import (
    BASE_BUSCA,
    BuscaIndisponivel,
    CrawlerCFM,
    parse_pagina,
    url_pdf,
)


def test_parse_le_json_embutido(html_busca: str) -> None:
    """A página traz os dados estruturados; não raspamos tabela."""
    resolucoes, total = parse_pagina(html_busca)
    assert len(resolucoes) == 10
    assert total == 2457
    primeira = resolucoes[0]
    assert primeira.numero and primeira.ano
    assert primeira.url_origem.startswith("https://sistemas.cfm.org.br/normas/visualizar/")


def test_parse_traz_vigencia(html_busca: str) -> None:
    """IS_REVOGADA vem pronto do portal: é a informação que mais importa."""
    resolucoes, _ = parse_pagina(html_busca)
    assert all(isinstance(r.vigente, bool) for r in resolucoes)


def test_parse_monta_url_do_pdf(html_busca: str) -> None:
    resolucoes, _ = parse_pagina(html_busca)
    assert resolucoes[0].url_pdf.endswith(f"/{resolucoes[0].numero}_{resolucoes[0].ano}.pdf")


def test_url_pdf_segue_o_padrao_confirmado() -> None:
    assert url_pdf("resolucoes", "BR", "2026", "2462").endswith(
        "/normas/arquivos/resolucoes/BR/2026/2462_2026.pdf"
    )


def test_pagina_sem_a_variavel_falha_explicito() -> None:
    """Se o portal mudar, é melhor quebrar com mensagem do que devolver vazio."""
    with pytest.raises(BuscaIndisponivel) as erro:
        parse_pagina("<html><body>nada aqui</body></html>")
    assert "portal pode ter mudado" in str(erro.value)


def test_json_corrompido_falha_explicito() -> None:
    html = "<script>let resultadoBuscaJson = {isso nao e json};\n</script>"
    with pytest.raises(BuscaIndisponivel):
        parse_pagina(html)


@respx.mock
async def test_varrer_respeita_o_limite_de_paginas(html_busca: str) -> None:
    rota = respx.get(BASE_BUSCA).mock(return_value=httpx.Response(200, text=html_busca))
    async with CrawlerCFM(delay_segundos=0) as crawler:
        resolucoes = await crawler.varrer(max_paginas=3)
    assert rota.call_count == 3
    assert len(resolucoes) == 30


@respx.mock
async def test_varrer_calcula_paginas_pelo_total(html_busca: str) -> None:
    """2457 itens, 10 por página: o crawler não pode parar na primeira."""
    respx.get(BASE_BUSCA).mock(return_value=httpx.Response(200, text=html_busca))
    async with CrawlerCFM(delay_segundos=0) as crawler:
        _, total = await crawler.pagina(1)
    assert -(-total // 10) == 246


def test_cache_nao_rebaixa(tmp_path: object) -> None:
    from pathlib import Path

    cache = CachePdf(Path(str(tmp_path)))
    url = "https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2026/2462_2026.pdf"
    assert cache.tem(url) is False
    cache.gravar(url, b"%PDF-1.4 conteudo")
    assert cache.tem(url) is True
    assert cache.ler(url) == b"%PDF-1.4 conteudo"
