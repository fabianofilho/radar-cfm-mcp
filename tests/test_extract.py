"""Extração de texto e filtro por palavra-chave."""

from __future__ import annotations

from radar_cfm_mcp.extract.parser import casa_palavras_chave, extrair_pdf, limpar

PALAVRAS = ("inteligência artificial", "telemedicina", "prontuário eletrônico", "algoritmo")


def test_pdf_vazio_sinaliza_incompleto() -> None:
    """Requisito: guardar o registro assim mesmo, sinalizando."""
    resultado = extrair_pdf(b"")
    assert resultado.metadados_incompletos is True
    assert resultado.texto == ""
    assert resultado.motivo is not None


def test_pdf_invalido_nao_levanta() -> None:
    """Um PDF corrompido não pode derrubar o sync inteiro."""
    resultado = extrair_pdf(b"isso nao e um pdf")
    assert resultado.metadados_incompletos is True
    assert "falhou" in (resultado.motivo or "")


def test_limpar_normaliza_sem_perder_paragrafo() -> None:
    assert limpar("linha  um\n\n\n\nlinha   dois") == "linha um\n\nlinha dois"
    assert limpar("com\xa0espaço\r\nquebrado") == "com espaço\nquebrado"


def test_palavras_chave_ignoram_caixa() -> None:
    assert casa_palavras_chave("Dispõe sobre TELEMEDICINA no Brasil", PALAVRAS)
    assert casa_palavras_chave("uso de Inteligência Artificial em diagnóstico", PALAVRAS)


def test_palavras_chave_nao_casam_a_esmo() -> None:
    assert not casa_palavras_chave("Dispõe sobre publicidade médica", PALAVRAS)


def test_palavra_vazia_nao_casa_tudo() -> None:
    """Palavra em branco na configuração não pode virar 'casa com qualquer coisa'."""
    assert not casa_palavras_chave("qualquer texto", ("", "   "))


def test_palavras_chave_do_env_separadas_por_virgula() -> None:
    """O .env traz uma lista separada por vírgula, não JSON."""
    from radar_cfm_mcp.config import Config

    config = Config(palavras_chave="telemedicina, algoritmo , ")
    assert config.palavras_chave == ("telemedicina", "algoritmo")
