"""Data de publicação a partir do texto da resolução.

O portal do CFM traz um campo ``DATA``, mas ele **não é a data de publicação**:
a Resolução 467/1972 vem com ``DATA='01/11/24'``, que é quando o registro entrou
no sistema deles. Usar aquele campo encheria ``data_publicacao`` com datas de
carga e faria o monitor de novidades devolver normas de 1972 como recentes, o
que é pior que devolver nada.

A data real está no texto, em três formatos que convivem no acervo:

- ``Publicado em: 17/09/2026`` nas recentes, sem citar o DOU;
- ``D.O.U. de 24 de setembro de 2019, Seção I, p.107`` no meio do acervo;
- ``D.O.U. - de 02/05/2005 - Seção I`` nas mais antigas.

Cobertura medida sobre as 2.457 resoluções: 97% das de 2020 em diante, 51% do
acervo inteiro. O que não tem data fica ``None``, e quem consome precisa dizer
isso em vez de tratar ausência como "não houve publicação".
"""

from __future__ import annotations

import re
from datetime import date

_MESES = {
    nome: numero
    for numero, nome in enumerate(
        (
            "janeiro",
            "fevereiro",
            "março",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
        ),
        start=1,
    )
}
_MESES["marco"] = 3

_ANCORA_DOU = r"(?:D\.?\s?O\.?\s?U\.?|Di[áa]rio\s+Oficial)"
_PUBLICADO_EM = re.compile(r"Publicad[oa]\s+em:?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)
_DOU_EXTENSO = re.compile(
    _ANCORA_DOU + r"[^\n]{0,80}?(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", re.I
)
_DOU_NUMERICA = re.compile(_ANCORA_DOU + r"[^\n]{0,80}?(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)

# O cabeçalho fica no começo. Mais adiante o texto cita OUTRAS normas com as
# datas delas, e foi assim que uma resolução de 2025 ganhou data de 1993.
_ALCANCE = 1500


def _montar(dia: str | int, mes: str | int, ano: str | int) -> date | None:
    ano = int(ano)
    if ano < 50:
        ano += 2000
    elif ano < 100:
        ano += 1900
    try:
        return date(ano, int(mes), int(dia))
    except ValueError:
        return None


def extrair_data_publicacao(texto: str | None, ano_norma: int | str) -> date | None:
    """Data em que a resolução saiu no Diário Oficial, se o texto disser.

    ``ano_norma`` é o ano do identificador e serve de guarda: uma data que
    destoe dele em mais de um ano veio de outra norma citada no texto, não desta.
    """
    if not texto:
        return None
    try:
        ano_norma = int(ano_norma)
    except (TypeError, ValueError):
        return None

    trecho = texto[:_ALCANCE]
    candidatas: list[date | None] = []

    if (achado := _PUBLICADO_EM.search(trecho)) is not None:
        candidatas.append(_montar(achado.group(1), achado.group(2), achado.group(3)))
    if (achado := _DOU_EXTENSO.search(trecho)) is not None:
        mes = _MESES.get(achado.group(2).lower())
        candidatas.append(_montar(achado.group(1), mes, achado.group(3)) if mes else None)
    if (achado := _DOU_NUMERICA.search(trecho)) is not None:
        candidatas.append(_montar(achado.group(1), achado.group(2), achado.group(3)))

    for candidata in candidatas:
        if candidata is not None and abs(candidata.year - ano_norma) <= 1:
            return candidata
    return None
