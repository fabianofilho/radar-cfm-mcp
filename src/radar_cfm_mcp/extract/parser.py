"""PDF da resolução para texto limpo.

Se a extração falhar ou vier vazia, o registro é guardado assim mesmo com
``metadados_incompletos=True``: perder a resolução inteira por causa de um PDF
problemático seria pior do que guardar só a ementa.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ESPACOS = re.compile(r"[ \t]+")
_LINHAS_VAZIAS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class TextoExtraido:
    """Resultado da extração de um PDF."""

    texto: str
    paginas: int
    metadados_incompletos: bool
    motivo: str | None = None


def limpar(bruto: str) -> str:
    """Normaliza espaços sem destruir a quebra de parágrafo."""
    texto = bruto.replace("\r\n", "\n").replace("\xa0", " ")
    texto = _ESPACOS.sub(" ", texto)
    return _LINHAS_VAZIAS.sub("\n\n", texto).strip()


def extrair_pdf(conteudo: bytes) -> TextoExtraido:
    """Texto completo de um PDF de resolução."""
    if not conteudo:
        return TextoExtraido(texto="", paginas=0, metadados_incompletos=True, motivo="PDF vazio")

    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            paginas = [pagina.extract_text() or "" for pagina in pdf.pages]
    except Exception as erro:  # noqa: BLE001 - PDF ruim não pode derrubar o sync
        logger.warning("falha ao extrair PDF: %s", type(erro).__name__)
        return TextoExtraido(
            texto="",
            paginas=0,
            metadados_incompletos=True,
            motivo=f"extração falhou: {type(erro).__name__}",
        )

    texto = limpar("\n".join(paginas))
    if not texto:
        return TextoExtraido(
            texto="",
            paginas=len(paginas),
            metadados_incompletos=True,
            motivo="PDF sem camada de texto (provavelmente digitalizado)",
        )
    return TextoExtraido(texto=texto, paginas=len(paginas), metadados_incompletos=False)


def casa_palavras_chave(texto: str, palavras: tuple[str, ...]) -> bool:
    """Se algum termo aparece no texto, ignorando caixa e acento simples."""
    alvo = texto.lower()
    return any(palavra.strip().lower() in alvo for palavra in palavras if palavra.strip())
