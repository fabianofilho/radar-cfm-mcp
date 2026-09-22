"""Modo connector: troca atômica da base e limite de requisições."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from radar_cfm_mcp.mcp_server.limite import LimitadorPorOrigem, origem_da_requisicao
from radar_cfm_mcp.store.db import aplicar_schema
from radar_cfm_mcp.store.troca import (
    BaseSuspeita,
    caminho_em_construcao,
    clonar_para_construcao,
    publicar,
    reverter,
)


def _base(caminho: Path, marca: str) -> None:
    conexao = duckdb.connect(str(caminho))
    aplicar_schema(conexao)
    conexao.execute(
        "INSERT INTO resolucoes (identificador, numero, ano, url_origem, url_pdf) "
        "VALUES (?, '1', '2020', 'u', 'p')",
        [marca],
    )
    conexao.close()


def _marca(caminho: Path) -> str:
    conexao = duckdb.connect(str(caminho), read_only=True)
    valor = conexao.execute("SELECT identificador FROM resolucoes").fetchone()
    conexao.close()
    assert valor is not None
    return str(valor[0])


# --- o problema que a troca existe para resolver -------------------------


def test_escritor_e_bloqueado_por_leitor(tmp_path: Path) -> None:
    """Escrever direto no arquivo servido falha: é a razão de existir a troca."""
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "antiga")

    leitor = duckdb.connect(str(servida), read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            duckdb.connect(str(servida))
    finally:
        leitor.close()


def test_troca_funciona_com_leitor_aberto(tmp_path: Path) -> None:
    """A coleta escreve ao lado e troca: o leitor aberto não impede nada."""
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "antiga")
    _base(caminho_em_construcao(servida), "nova")

    leitor = duckdb.connect(str(servida), read_only=True)
    try:
        publicar(servida)  # não levanta, mesmo com leitor aberto
    finally:
        leitor.close()

    assert _marca(servida) == "nova"


def test_leitor_aberto_antes_da_troca_ve_a_versao_antiga(tmp_path: Path) -> None:
    """Quem já abriu continua no inode antigo, consistente durante a requisição."""
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "antiga")
    _base(caminho_em_construcao(servida), "nova")

    leitor = duckdb.connect(str(servida), read_only=True)
    publicar(servida)
    antes = leitor.execute("SELECT identificador FROM resolucoes").fetchone()
    leitor.close()

    assert antes is not None and antes[0] == "antiga"
    assert _marca(servida) == "nova"  # quem abrir agora pega a nova


# --- publicação ----------------------------------------------------------


def test_publicar_guarda_a_anterior(tmp_path: Path) -> None:
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "antiga")
    _base(caminho_em_construcao(servida), "nova")

    publicar(servida)
    assert (tmp_path / "cfm.duckdb.anterior").exists()
    assert not caminho_em_construcao(servida).exists()


def test_reverter_volta_a_anterior(tmp_path: Path) -> None:
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "antiga")
    _base(caminho_em_construcao(servida), "nova")
    publicar(servida)
    assert _marca(servida) == "nova"

    reverter(servida)
    assert _marca(servida) == "antiga"


def test_publicar_sem_base_nova_falha_explicito(tmp_path: Path) -> None:
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "antiga")
    with pytest.raises(FileNotFoundError) as erro:
        publicar(servida)
    assert "nada para publicar" in str(erro.value)


def test_reverter_sem_anterior_falha_explicito(tmp_path: Path) -> None:
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "unica")
    with pytest.raises(FileNotFoundError):
        reverter(servida)


def test_em_construcao_fica_no_mesmo_diretorio(tmp_path: Path) -> None:
    """os.replace só é atômico dentro do mesmo sistema de arquivos."""
    servida = tmp_path / "sub" / "cfm.duckdb"
    assert caminho_em_construcao(servida).parent == servida.parent


# --- limite de requisições ----------------------------------------------


def test_limite_deixa_passar_ate_o_teto() -> None:
    limitador = LimitadorPorOrigem(3)
    assert [limitador.permitir("1.2.3.4") for _ in range(4)] == [True, True, True, False]


def test_limite_e_por_origem() -> None:
    """Um cliente em laço não pode consumir o limite dos outros."""
    limitador = LimitadorPorOrigem(2)
    assert limitador.permitir("1.1.1.1") and limitador.permitir("1.1.1.1")
    assert not limitador.permitir("1.1.1.1")
    assert limitador.permitir("2.2.2.2")


def test_origem_usa_forwarded_for_quando_ha_proxy() -> None:
    """Atrás de proxy, o IP do socket é o do proxy: todos viram a mesma origem."""
    scope = {
        "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")],
        "client": ("10.0.0.1", 5000),
    }
    assert origem_da_requisicao(scope) == "203.0.113.9"


def test_origem_cai_para_o_socket_sem_proxy() -> None:
    assert origem_da_requisicao({"headers": [], "client": ("198.51.100.7", 1234)}) == "198.51.100.7"


def test_origem_desconhecida_nao_quebra() -> None:
    assert origem_da_requisicao({}) == "desconhecido"


def test_teto_global_protege_independente_da_origem() -> None:
    """O teto que importa para tráfego vindo do Claude: ele chega de poucos IPs."""
    limitador = LimitadorPorOrigem(limite_por_minuto=100, limite_global_por_minuto=3)
    assert all(limitador.permitir(f"10.0.0.{i}") for i in range(3))
    assert not limitador.permitir("10.0.0.99")  # origem nova, mas o global estourou
    assert limitador.motivo_ultima_recusa == "global"


def test_motivo_distingue_os_dois_tetos() -> None:
    limitador = LimitadorPorOrigem(limite_por_minuto=1, limite_global_por_minuto=100)
    assert limitador.permitir("1.1.1.1")
    assert not limitador.permitir("1.1.1.1")
    assert limitador.motivo_ultima_recusa == "origem"


def test_sem_teto_global_so_vale_o_por_origem() -> None:
    limitador = LimitadorPorOrigem(limite_por_minuto=2, limite_global_por_minuto=0)
    assert all(limitador.permitir(f"10.0.0.{i}") for i in range(50))


# --- salvaguarda contra publicar base truncada ---------------------------


def _base_com(caminho: Path, quantas: int) -> None:
    conexao = duckdb.connect(str(caminho))
    aplicar_schema(conexao)
    conexao.executemany(
        "INSERT INTO resolucoes (identificador, numero, ano, url_origem, url_pdf) "
        "VALUES (?, ?, '2020', 'u', 'p')",
        [[f"{i}/2020", str(i)] for i in range(quantas)],
    )
    conexao.close()


def test_recusa_publicar_base_que_encolheu(tmp_path: Path) -> None:
    """Coleta truncada por rede ruim não pode substituir a base boa em silêncio."""
    servida = tmp_path / "cfm.duckdb"
    _base_com(servida, 2457)
    _base_com(caminho_em_construcao(servida), 20)

    with pytest.raises(BaseSuspeita) as erro:
        publicar(servida)
    assert "20 linha(s)" in str(erro.value) and "2457" in str(erro.value)
    # a base servida continua intacta
    conexao = duckdb.connect(str(servida), read_only=True)
    assert conexao.execute("SELECT count(*) FROM resolucoes").fetchone()[0] == 2457
    conexao.close()


def test_forcar_publica_mesmo_menor(tmp_path: Path) -> None:
    """Quando a fonte encolheu de verdade, quem decide é quem roda."""
    servida = tmp_path / "cfm.duckdb"
    _base_com(servida, 100)
    _base_com(caminho_em_construcao(servida), 5)
    publicar(servida, forcar=True)

    conexao = duckdb.connect(str(servida), read_only=True)
    assert conexao.execute("SELECT count(*) FROM resolucoes").fetchone()[0] == 5
    conexao.close()


def test_pequena_variacao_para_menos_e_tolerada(tmp_path: Path) -> None:
    """Registro sai da fonte o tempo todo: 5% a menos não é motivo de alarme."""
    servida = tmp_path / "cfm.duckdb"
    _base_com(servida, 1000)
    _base_com(caminho_em_construcao(servida), 960)
    publicar(servida)  # não levanta


def test_primeira_publicacao_nao_exige_forcar(tmp_path: Path) -> None:
    """Sem base servida ainda, não há com o que comparar."""
    servida = tmp_path / "cfm.duckdb"
    _base_com(caminho_em_construcao(servida), 10)
    publicar(servida)
    assert servida.exists()


def test_clone_nao_perde_o_que_a_varredura_de_hoje_nao_trouxe(tmp_path: Path) -> None:
    """Resolução que saiu do portal continua na base nova: a coleta faz upsert por cima."""
    servida = tmp_path / "cfm.duckdb"
    _base(servida, "resolucao-antiga")

    nova = clonar_para_construcao(servida)

    conexao = duckdb.connect(str(nova), read_only=True)
    try:
        assert conexao.execute(
            "SELECT count(*) FROM resolucoes WHERE identificador = 'resolucao-antiga'"
        ).fetchone() == (1,)
    finally:
        conexao.close()


def test_clone_sem_base_servida_nao_e_erro(tmp_path: Path) -> None:
    """Primeira varredura da vida: não há de onde clonar, e o caminho volta vazio."""
    assert not clonar_para_construcao(tmp_path / "ainda-nao-existe.duckdb").exists()


def test_sem_host_publico_mantem_o_padrao_do_sdk() -> None:
    """Sem nome público declarado, quem decide é o SDK: só loopback."""
    from radar_cfm_mcp.config import Config
    from radar_cfm_mcp.mcp_server.server import _seguranca_de_transporte

    assert _seguranca_de_transporte(Config(http_hosts_publicos=[])) is None


def test_host_publico_entra_sem_derrubar_o_loopback() -> None:
    """Declarar o nome do túnel não pode cortar o acesso local, que é como se testa."""
    from radar_cfm_mcp.config import Config
    from radar_cfm_mcp.mcp_server.server import _seguranca_de_transporte

    regras = _seguranca_de_transporte(Config(http_hosts_publicos=["mcp.exemplo.ts.net"]))
    assert regras is not None
    assert regras.enable_dns_rebinding_protection is True
    assert "mcp.exemplo.ts.net" in regras.allowed_hosts
    assert "127.0.0.1:*" in regras.allowed_hosts
    assert "https://mcp.exemplo.ts.net" in regras.allowed_origins


def test_host_de_fora_da_lista_continua_recusado() -> None:
    """A proteção contra DNS rebinding continua valendo para quem não foi declarado."""
    from mcp.server.transport_security import TransportSecurityMiddleware

    from radar_cfm_mcp.config import Config
    from radar_cfm_mcp.mcp_server.server import _seguranca_de_transporte

    guarda = TransportSecurityMiddleware(
        _seguranca_de_transporte(Config(http_hosts_publicos=["mcp.exemplo.ts.net"]))
    )
    assert guarda._validate_host("mcp.exemplo.ts.net") is True
    assert guarda._validate_host("mcp.exemplo.ts.net:443") is True
    assert guarda._validate_host("site-do-atacante.exemplo") is False
    assert guarda._validate_host(None) is False
