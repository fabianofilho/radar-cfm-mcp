"""Data de publicação a partir do texto da resolução.

O portal do CFM traz um campo ``DATA``, mas ele **não é a data de publicação**:
a Resolução 467/1972 vem com ``DATA='01/11/24'``, que é quando o registro entrou
no sistema deles. Usar aquele campo encheria ``data_publicacao`` com datas de
carga e faria o monitor de novidades devolver normas de 1972 como recentes, o
que é pior que devolver nada.

A data real está no texto, no cabeçalho, depois de uma âncora ("Publicada",
"D.O.U.", "D.O." ou "Diário Oficial") e na mesma linha dela. Os formatos que
convivem no acervo:

- ``Publicado em: 17/09/2026`` nas recentes, sem citar o DOU;
- ``D.O.U. de 24 de setembro de 2019, Seção I, p.107`` no meio do acervo;
- ``D.O.U. de 15 Jan 2024`` e ``29 set. 2014``, com o mês abreviado;
- ``D.O.U. de 23 fevereiro de 2022``, sem o primeiro "de";
- ``D.O.U. - de 02/05/2005 - Seção I`` e ``Diário Oficial de 21-5-62`` nas antigas;
- ``Publica do e m: 21/09/2023``, quando a extração do PDF quebra as palavras.

Cobertura medida sobre as 2.457 resoluções em 24/09/2026: 2.109 com data (86%).
O que não tem data fica ``None``, e quem consome precisa dizer isso em vez de
tratar ausência como "não houve publicação". Boa parte do que sobra é cabeçalho
sem data no próprio PDF ("Publicada no D.O. Seção I, Parte II de", e nada depois).
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
# "29 set. 2014": as abreviaturas de três letras também aparecem no cabeçalho.
_ABREVIADOS = {nome[:3]: numero for nome, numero in _MESES.items()}

# Onde o cabeçalho diz que a norma saiu no Diário Oficial. "Publica" tolera
# espaço entre as letras porque a extração de alguns PDFs devolve "Publica do e m".
_ANCORA = re.compile(
    r"p\s?u\s?b\s?l\s?i\s?c\s?a\s?d\s?[oa]"
    r"|D\.?\s?O\.?\s?U\.?"
    r"|D\.\s?O\."
    r"|Di[áa]rio\s+Oficial",
    re.I,
)
_NUMERICA = re.compile(r"(\d{1,2})\s?[/\-.]\s?(\d{1,2})\s?[/\-.]\s?(\d{2,4})")
# "24 de setembro de 2019", "23 fevereiro de 2022", "15 Jan 2024", "29 set. 2014",
# "1º de agosto de 2011", "7 de junho de1958".
_EXTENSO = re.compile(r"(\d{1,2})[º°]?\s*(?:de\s*)?([a-zç]{3,9})\.?\s*(?:de\s*)?(\d{4})", re.I)

# O cabeçalho fica no começo. Mais adiante o texto cita OUTRAS normas com as
# datas delas, e foi assim que uma resolução de 2025 ganhou data de 1993.
_ALCANCE = 1500
# A data vem na mesma linha da âncora, logo depois dela. Sem o limite de linha,
# "entrará em vigor na data de sua publicação.\nRio de Janeiro, 12 de abril"
# faria a data de assinatura passar por data de publicação.
_JANELA = 100


def _ano_completo(ano: str, ano_norma: int) -> int:
    """Ano com quatro dígitos, escolhendo o século mais perto do ano da norma."""
    valor = int(ano)
    if valor >= 100:
        return valor
    return min((1900 + valor, 2000 + valor), key=lambda a: abs(a - ano_norma))


def _montar(dia: str | int, mes: str | int, ano: str, ano_norma: int) -> date | None:
    try:
        return date(_ano_completo(ano, ano_norma), int(mes), int(dia))
    except ValueError:
        return None


def _mes(nome: str) -> int | None:
    nome = nome.lower()
    if nome in _MESES:
        return _MESES[nome]
    return _ABREVIADOS.get(nome) if len(nome) == 3 else None


def _data_na_janela(janela: str, ano_norma: int) -> date | None:
    """Primeira data da janela, em qualquer dos formatos do acervo."""
    # "24/1 2/73": a extração às vezes quebra o número com um espaço no meio.
    compacta = re.sub(r"(?<=\d)\s(?=\d)", "", janela)
    achados: list[tuple[int, date | None]] = []
    if (numerica := _NUMERICA.search(compacta)) is not None:
        d, m, a = numerica.groups()
        achados.append((numerica.start(), _montar(d, m, a, ano_norma)))
    for extenso in _EXTENSO.finditer(janela):
        mes = _mes(extenso.group(2))
        if mes is not None:
            achados.append(
                (extenso.start(), _montar(extenso.group(1), mes, extenso.group(3), ano_norma))
            )
            break
    # As posições vêm de textos levemente diferentes (com e sem espaço), mas a
    # compactação só encurta, então a ordem entre elas se mantém o bastante.
    for _, candidata in sorted(achados, key=lambda par: par[0]):
        if candidata is not None:
            return candidata
    return None


def extrair_data_publicacao(texto: str | None, ano_norma: int | str) -> date | None:
    """Data em que a resolução saiu no Diário Oficial, se o texto disser.

    ``ano_norma`` é o ano do identificador e serve de guarda: uma data que
    destoe dele em mais de um ano veio de outra norma citada no texto, não desta.
    A tolerância de um ano cobre a norma aprovada em dezembro e publicada em
    janeiro.
    """
    if not texto:
        return None
    try:
        ano_norma = int(ano_norma)
    except (TypeError, ValueError):
        return None

    trecho = texto[:_ALCANCE]
    for ancora in _ANCORA.finditer(trecho):
        resto = trecho[ancora.end() : ancora.end() + _JANELA]
        janela = resto.split("\n", 1)[0]
        candidata = _data_na_janela(janela, ano_norma)
        if candidata is not None and abs(candidata.year - ano_norma) <= 1:
            return candidata
    return None
