"""Varre o sistema de busca de normas do CFM.

Descoberta que muda o desenho (confirmada em 20/09/2026): a página de resultados
traz os dados **estruturados em JSON**, numa variável ``resultadoBuscaJson``
embutida no HTML. Não é preciso raspar tabela nem adivinhar layout, e o JSON já
inclui ``IS_REVOGADA``, que é a informação de vigência que mais importa.

O parâmetro de busca textual na URL é ignorado pelo portal (o COUNT não muda),
então o filtro por palavra-chave é aplicado localmente sobre a ementa.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

BASE_BUSCA = "https://portal.cfm.org.br/buscar-normas-cfm-e-crm/"
BASE_PDF = "https://sistemas.cfm.org.br/normas/arquivos"
BASE_VISUALIZAR = "https://sistemas.cfm.org.br/normas/visualizar"

# Identifica o projeto para quem administra o portal, com link para o repositorio.
# Um coletor publico anonimo e ma cidadania: se algo incomodar do outro lado,
# precisa haver como descobrir o que e e falar com quem mantem.
USER_AGENT = "radar-cfm-mcp/0.1 (+https://github.com/fabianofilho/radar-cfm-mcp)"
POR_PAGINA = 10

_RESULTADO = re.compile(r"let\s+resultadoBuscaJson\s*=\s*(\{.*?\});\s*\n", re.DOTALL)


class BuscaIndisponivel(RuntimeError):
    """O portal respondeu, mas não no formato esperado."""


@dataclass(frozen=True)
class Resolucao:
    """Uma norma do CFM, como o portal a devolve."""

    numero: str
    ano: str
    # Campo DATA do portal. NAO e a data de publicacao no DOU: a Resolucao
    # 467/1972 vem com "01/11/24", que e quando entrou no sistema do CFM. Fica
    # aqui por ser o que a fonte da, mas quem precisa da publicacao usa
    # extract.datas.extrair_data_publicacao, que le do texto.
    data_no_portal: date | None
    ementa: str
    vigente: bool
    revogada_por: str | None
    url_origem: str
    url_pdf: str

    @property
    def identificador(self) -> str:
        return f"{self.numero}/{self.ano}"


def _data(bruto: str | None) -> date | None:
    if not bruto:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(bruto.strip(), formato).date()
        except ValueError:
            continue
    return None


def url_pdf(sname: str, uf: str, ano: str, numero: str) -> str:
    """Padrão confirmado: .../arquivos/resolucoes/BR/2026/2462_2026.pdf"""
    return f"{BASE_PDF}/{sname}/{uf}/{ano}/{numero}_{ano}.pdf"


def parse_pagina(html: str) -> tuple[list[Resolucao], int]:
    """Extrai as resoluções e o total de itens da busca.

    Raises:
        BuscaIndisponivel: quando a variável de resultados não está na página,
            sinal de que o portal mudou e o parser precisa ser revisto.
    """
    achado = _RESULTADO.search(html)
    if achado is None:
        raise BuscaIndisponivel(
            "resultadoBuscaJson não encontrado na página do CFM; o portal pode ter mudado"
        )
    try:
        bruto = json.loads(achado.group(1))
    except json.JSONDecodeError as erro:
        raise BuscaIndisponivel("resultadoBuscaJson não era JSON válido") from erro

    resolucoes: list[Resolucao] = []
    total = 0
    for item in bruto.values():
        total = max(total, int(item.get("COUNT") or 0))
        numero = str(item.get("NUMERO") or "").strip()
        ano = str(item.get("ANO") or "").strip()
        if not numero or not ano:
            continue
        sname = str(item.get("SNAME") or "resolucoes")
        uf = str(item.get("RAW_UF") or "BR")
        revogada = str(item.get("REVOGADA") or "N").upper() == "S"
        numero_revogada = item.get("NUMERO_REVOGADA")
        ano_revogada = item.get("ANO_REVOGADA")
        resolucoes.append(
            Resolucao(
                numero=numero,
                ano=ano,
                data_no_portal=_data(item.get("DATA")),
                ementa=str(item.get("RESUMO") or "").strip(),
                vigente=not revogada,
                revogada_por=(
                    f"{numero_revogada}/{ano_revogada}"
                    if numero_revogada and ano_revogada
                    else None
                ),
                url_origem=f"{BASE_VISUALIZAR}/{sname}/{uf}/{ano}/{numero}",
                url_pdf=url_pdf(sname, uf, ano, numero),
            )
        )
    return resolucoes, total


class CrawlerCFM:
    """Busca paginada, com intervalo entre requisições."""

    def __init__(
        self,
        *,
        delay_segundos: float = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._delay = delay_segundos
        self._client = client
        self._client_proprio = client is None

    async def __aenter__(self) -> CrawlerCFM:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client_proprio:
            await self._client.aclose()
            self._client = None

    async def pagina(self, numero_pagina: int) -> tuple[list[Resolucao], int]:
        """Uma página de resultados (10 itens)."""
        resposta = await self._exigir_client().get(
            BASE_BUSCA, params={"tipo[0]": "R", "uf": "BR", "pagina": numero_pagina}
        )
        resposta.raise_for_status()
        return parse_pagina(resposta.text)

    async def varrer(self, *, max_paginas: int | None = None) -> list[Resolucao]:
        """Percorre as páginas respeitando o delay configurado.

        ``max_paginas`` limita a varredura, útil para o modo incremental, já que
        as resoluções mais recentes vêm primeiro.
        """
        primeira, total = await self.pagina(1)
        todas = list(primeira)
        paginas = -(-total // POR_PAGINA) if total else 1
        if max_paginas is not None:
            paginas = min(paginas, max_paginas)
        logger.info("CFM: %d resoluções em %d páginas", total, paginas)

        for numero_pagina in range(2, paginas + 1):
            await asyncio.sleep(self._delay)
            atual, _ = await self.pagina(numero_pagina)
            todas.extend(atual)
            if numero_pagina % 25 == 0:
                logger.info("CFM: página %d/%d", numero_pagina, paginas)
        return todas

    async def baixar_pdf(self, resolucao: Resolucao) -> bytes:
        """Baixa o PDF da resolução."""
        await asyncio.sleep(self._delay)
        resposta = await self._exigir_client().get(resolucao.url_pdf)
        resposta.raise_for_status()
        return resposta.content

    def _exigir_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("CrawlerCFM precisa ser usado como 'async with'")
        return self._client
