"""Vigência além da revogação: normas suspensas.

O portal do CFM marca revogação num campo estruturado (``IS_REVOGADA``), mas
**suspensão só aparece como texto colado no fim da ementa**, assim:

    Define e disciplina a medicina do sono como ato médico exclusivo. [RESOLUÇÂO SUSPENSA]
    Dispõe sobre atestados médicos ... (RESOLUÇÃO SUSPENSA)
    ... (PARCIALMENTE SUSPENSA)
    ... (Atenção dos dispositivos suspensos por decisão judicial, artigos 10, 12
         e o § 2º do artigo 15)

Para quem vai aplicar a norma, suspensa tem o mesmo efeito prático de revogada,
e o campo ``vigente`` sozinho não enxerga isso.

**A marcação está sempre entre parênteses ou colchetes**, e é isso que a separa
do assunto da norma: a Resolução 2016/2013 "dispõe sobre a suspensão da
Resolução CRM/DF nº 344/13" e não está suspensa. Procurar a palavra solta no
texto daria esse falso positivo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Trechos entre parenteses ou colchetes, que e onde o CFM cola o status.
_ENTRE_DELIMITADORES = re.compile(r"[\(\[]([^\)\]]{0,300})[\)\]]")
_PARCIAL = ("parcial", "dispositivo", "artigo")


@dataclass(frozen=True)
class Suspensao:
    """O que a ementa diz sobre a norma estar suspensa."""

    suspensa: bool
    parcial: bool
    nota: str | None


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    ).lower()


def detectar_suspensao(ementa: str | None) -> Suspensao:
    """Lê a marcação de suspensão que o CFM cola no fim da ementa."""
    if not ementa:
        return Suspensao(False, False, None)

    for achado in _ENTRE_DELIMITADORES.finditer(ementa):
        conteudo = achado.group(1).strip()
        normalizado = _sem_acento(conteudo)
        if "suspens" not in normalizado:
            continue
        parcial = any(marca in normalizado for marca in _PARCIAL)
        return Suspensao(True, parcial, conteudo)
    return Suspensao(False, False, None)
