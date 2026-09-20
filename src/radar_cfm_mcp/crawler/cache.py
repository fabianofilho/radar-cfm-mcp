"""Cache de PDFs já baixados, por URL.

Nunca baixar o mesmo PDF duas vezes: é um portal de conselho profissional, não
uma API feita para volume.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class CachePdf:
    """Guarda os PDFs em disco, nomeados pelo hash da URL."""

    def __init__(self, diretorio: Path | str) -> None:
        self._dir = Path(diretorio)

    def _caminho(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self._dir / f"{digest}.pdf"

    def tem(self, url: str) -> bool:
        return self._caminho(url).exists()

    def ler(self, url: str) -> bytes | None:
        caminho = self._caminho(url)
        if not caminho.exists():
            return None
        return caminho.read_bytes()

    def gravar(self, url: str, conteudo: bytes) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        caminho = self._caminho(url)
        caminho.write_bytes(conteudo)
        logger.debug("PDF cacheado: %s", caminho.name)
        return caminho
