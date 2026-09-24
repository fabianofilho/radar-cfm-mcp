"""Tools: consulta por tema, monitoramento e vigência."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import duckdb
import pytest

from radar_cfm_mcp.mcp_server.tools.resolucoes import (
    consultar_resolucao_cfm,
    monitorar_novas_resolucoes,
)
from radar_cfm_mcp.store.db import aplicar_schema, conectar, reindexar_fts
from radar_cfm_mcp.store.queries import buscar_por_tema, gravar, publicadas_no_periodo


def _registro(**campos: Any) -> dict[str, Any]:
    base = {
        "identificador": "1/2020",
        "numero": "1",
        "ano": "2020",
        "data_publicacao": date.today() - timedelta(days=10),
        "ementa": "",
        "texto_completo": None,
        "vigente": True,
        "revogada_por": None,
        "url_origem": "https://sistemas.cfm.org.br/normas/visualizar/resolucoes/BR/2020/1",
        "url_pdf": "https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2020/1_2020.pdf",
        "metadados_incompletos": False,
    }
    base.update(campos)
    return base


def test_gravar_e_idempotente(db: duckdb.DuckDBPyConnection) -> None:
    linha = _registro(ementa="Dispõe sobre telemedicina")
    assert gravar(db, [linha]) == (1, 0)
    assert gravar(db, [linha]) == (0, 1)
    assert db.execute("SELECT count(*) FROM resolucoes").fetchone()[0] == 1


def test_gravar_deduplica_no_mesmo_lote(db: duckdb.DuckDBPyConnection) -> None:
    linha = _registro(ementa="a")
    novos, _ = gravar(db, [linha, {**linha, "ementa": "b"}])
    assert novos == 1
    assert db.execute("SELECT ementa FROM resolucoes").fetchone()[0] == "b"


def test_busca_por_tema_acha_na_ementa(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            _registro(identificador="1/2020", ementa="Dispõe sobre telemedicina"),
            _registro(
                identificador="2/2021", numero="2", ano="2021", ementa="Dispõe sobre publicidade"
            ),
        ],
    )
    achados = buscar_por_tema(db, "telemedicina")
    assert [a["identificador"] for a in achados] == ["1/2020"]


def test_busca_omite_revogadas_por_padrao(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            _registro(
                identificador="1/2020", ementa="telemedicina", vigente=False, revogada_por="9/2024"
            ),
            _registro(identificador="2/2021", numero="2", ano="2021", ementa="telemedicina"),
        ],
    )
    assert [a["identificador"] for a in buscar_por_tema(db, "telemedicina")] == ["2/2021"]
    assert len(buscar_por_tema(db, "telemedicina", apenas_vigentes=False)) == 2


def test_periodo_exclui_antigas(db: duckdb.DuckDBPyConnection) -> None:
    gravar(
        db,
        [
            _registro(identificador="1/2020", data_publicacao=date.today() - timedelta(days=5)),
            _registro(
                identificador="2/2019",
                numero="2",
                ano="2019",
                data_publicacao=date.today() - timedelta(days=400),
            ),
        ],
    )
    assert [a["identificador"] for a in publicadas_no_periodo(db, dias=30)] == ["1/2020"]


def test_fts_indexa_e_a_busca_continua_funcionando(db: duckdb.DuckDBPyConnection) -> None:
    """Se o FTS não estiver disponível, a busca cai para LIKE, mas responde."""
    gravar(db, [_registro(ementa="Dispõe sobre inteligência artificial em medicina")])
    reindexar_fts(db)  # pode falhar offline; a busca precisa funcionar de qualquer jeito
    assert len(buscar_por_tema(db, "inteligência artificial")) == 1


async def test_base_inexistente_avisa_em_vez_de_quebrar(caminho_db: str) -> None:
    resposta = await consultar_resolucao_cfm("telemedicina", caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None and "sincronizada" in resposta.aviso


async def test_consulta_traz_fonte_e_trecho(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            [
                _registro(
                    ementa="Dispõe sobre telemedicina",
                    texto_completo=(
                        "Artigo 1. A telemedicina é o exercício da medicina "
                        "mediado por tecnologias."
                    ),
                )
            ],
        )
    resposta = await consultar_resolucao_cfm("telemedicina", caminho_db=caminho_db)
    assert resposta.total == 1
    resultado = resposta.resultados[0]
    assert resultado.url_origem.startswith("https://")
    assert resultado.trecho_relevante is not None
    assert "telemedicina" in resultado.trecho_relevante.lower()
    assert resposta.aviso is not None and "confira o texto oficial" in resposta.aviso


async def test_revogada_vem_marcada(caminho_db: str) -> None:
    """Sinalizar revogação é requisito: o usuário não pode citar norma revogada."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_registro(ementa="telemedicina", vigente=False, revogada_por="9/2024")])
    resposta = await consultar_resolucao_cfm(
        "telemedicina", caminho_db=caminho_db, apenas_vigentes=False
    )
    assert resposta.resultados[0].vigente is False
    assert resposta.resultados[0].revogada_por == "9/2024"


async def test_monitorar_filtra_por_tema(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            [
                _registro(identificador="1/2026", ementa="Dispõe sobre telemedicina"),
                _registro(identificador="2/2026", numero="2", ementa="Dispõe sobre honorários"),
            ],
        )
    resposta = await monitorar_novas_resolucoes(
        90, caminho_db=caminho_db, palavras_chave=("telemedicina",)
    )
    assert [r.identificador for r in resposta.resultados] == ["1/2026"]


async def test_dias_invalido(caminho_db: str) -> None:
    resposta = await monitorar_novas_resolucoes(0, caminho_db=caminho_db)
    assert resposta.total == 0
    assert resposta.aviso is not None


def test_trecho_none_quando_o_termo_nao_esta_no_texto() -> None:
    """Devolver a abertura do documento seria citação errada com cara de resposta."""
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    texto = "O CONSELHO FEDERAL DE MEDICINA, no uso das atribuições que lhe confere a lei..."
    assert _trecho(texto, "prazo de resposta em telediagnóstico") is None


def test_trecho_acha_pela_palavra_mais_longa_da_frase() -> None:
    """Quem pergunta escreve frase; o texto legal traz uma palavra só."""
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    texto = (
        "Art. 2º A TELEMEDICINA, em tempo real on-line (síncrona) ou off-line "
        "(assíncrona), por multimeios em tecnologia, é permitida."
    )
    achado = _trecho(texto, "telediagnóstico assíncrono")
    assert achado is not None
    assert "assíncrona" in achado


def test_trecho_ignora_palavra_curta_demais_para_ancorar() -> None:
    """'de' casaria em qualquer lugar e devolveria trecho aleatório."""
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    assert _trecho("Texto qualquer sem o assunto pedido aqui.", "de ao") is None


def test_trecho_tolera_desinencia_mas_nao_prefixo_curto() -> None:
    """Aceitar 'assíncrono' onde há 'assíncrona' sem deixar 'tele' casar com tudo."""
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    # Radical de 5+ letras: casa, é a mesma palavra flexionada.
    assert _trecho("fica permitida a modalidade assíncrona", "assíncrono") is not None
    # 'tele' sozinho não pode ancorar num texto que só fala de telefone.
    assert _trecho("o telefone do consultório deve constar", "telessaúde") is None


@pytest.mark.asyncio
async def test_total_e_quantas_casam_nao_quantas_vieram(caminho_db: str) -> None:
    """Dizer 'total: 10' com 72 na base faz quem lê achar que viu tudo."""
    with conectar(caminho_db) as conexao:
        aplicar_schema(conexao)
        conexao.executemany(
            "INSERT INTO resolucoes (identificador, numero, ano, url_origem, url_pdf, "
            "ementa, vigente) VALUES (?, ?, '2020', 'u', 'p', 'trata de publicidade', true)",
            [[f"{i}/2020", str(i)] for i in range(30)],
        )

    r = await consultar_resolucao_cfm("publicidade", caminho_db=caminho_db, limite=10)

    assert r.total == 30, "total tem que ser o universo que casa, nao a pagina"
    assert r.retornados == 10
    assert r.truncado is True
    assert r.aviso is not None and "30" in r.aviso


@pytest.mark.asyncio
async def test_avisa_quando_a_busca_viu_so_a_ementa(caminho_db: str) -> None:
    """Sem texto integral, 'trecho nulo' não significa 'a norma não trata disso'."""
    with conectar(caminho_db) as conexao:
        aplicar_schema(conexao)
        conexao.execute(
            "INSERT INTO resolucoes (identificador, numero, ano, url_origem, url_pdf, "
            "ementa, vigente) VALUES ('1/2020', '1', '2020', 'u', 'p', "
            "'dispoe sobre prontuario', true)"
        )

    r = await consultar_resolucao_cfm("prontuario", caminho_db=caminho_db)

    assert r.resultados[0].texto_completo_disponivel is False
    assert r.resultados[0].trecho_relevante is None
    assert r.aviso is not None and "só" in r.aviso and "ementa" in r.aviso


def test_coleta_sem_pdf_nao_apaga_texto_ja_baixado(db: duckdb.DuckDBPyConnection) -> None:
    """A coleta da madrugada não baixa PDF; se sobrescrevesse, perderíamos tudo."""
    gravar(db, [_registro(ementa="telemedicina", texto_completo="Art. 1 o texto inteiro")])
    gravar(db, [_registro(ementa="telemedicina atualizada", texto_completo=None)])

    linha = db.execute(
        "SELECT ementa, texto_completo FROM resolucoes WHERE identificador = '1/2020'"
    ).fetchone()
    assert linha[0] == "telemedicina atualizada", "a ementa nova tem que entrar"
    assert linha[1] == "Art. 1 o texto inteiro", "o texto antigo tem que sobreviver"


def test_texto_novo_substitui_o_antigo(db: duckdb.DuckDBPyConnection) -> None:
    """Preservar não pode virar congelar: texto novo tem que entrar."""
    gravar(db, [_registro(texto_completo="versão antiga")])
    gravar(db, [_registro(texto_completo="versão nova")])

    linha = db.execute("SELECT texto_completo FROM resolucoes").fetchone()
    assert linha[0] == "versão nova"


def test_trecho_prefere_onde_as_palavras_aparecem_juntas() -> None:
    """A 1a ocorrencia de 'telemedicina' e o cabecalho, que nao responde nada."""
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    texto = (
        "RESOLUÇÃO CFM 2314/2022. Define e regulamenta a telemedicina. "
        + ("Considerando o parecer anterior. " * 20)
        + "Art. 9 O médico deve fazer o registro em prontuário de cada "
        "atendimento por telemedicina, guardando o conteúdo."
    )
    achado = _trecho(texto, "registro em prontuário telemedicina")
    assert achado is not None
    assert "prontuário" in achado, "pegou o cabeçalho em vez do artigo"
    assert "RESOLUÇÃO CFM" not in achado


def test_trecho_com_uma_palavra_so_continua_na_primeira_ocorrencia() -> None:
    """Sem concorrência entre palavras, o comportamento antigo vale."""
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    achado = _trecho("abertura qualquer. Art. 1 trata de telemedicina.", "telemedicina")
    assert achado is not None and "telemedicina" in achado


def test_data_de_publicacao_sai_do_texto_nos_tres_formatos() -> None:
    """O acervo mistura três jeitos de escrever a mesma coisa."""
    from radar_cfm_mcp.extract.datas import extrair_data_publicacao as extrair

    assert extrair("RESOLUÇÃO CFM N° 2.471. Publicado em: 17/09/2026 | Edição: 176", 2026) == date(
        2026, 9, 17
    )
    assert extrair("(Publicada no D.O.U. de 24 de setembro de 2019, Seção I, p.107)", 2019) == date(
        2019, 9, 24
    )
    assert extrair("(D.O.U. - de 02/05/2005 - Seção I - Pág. 88)", 2005) == date(2005, 5, 2)


def test_data_de_outra_norma_citada_nao_entra() -> None:
    """Resolução de 2025 citando uma de 1992 não é publicada em 1992."""
    from radar_cfm_mcp.extract.datas import extrair_data_publicacao as extrair

    texto = "Altera a Resolução CFM 1.355, publicada no D.O.U. de 11 de setembro de 1992"
    assert extrair(texto, 2025) is None


@pytest.mark.asyncio
async def test_monitor_declara_quantas_ficaram_sem_data(caminho_db: str) -> None:
    """Zero resultados com muitas sem data é 'não sei', não 'nada foi publicado'."""
    with conectar(caminho_db) as conexao:
        aplicar_schema(conexao)
        gravar(conexao, [_registro(identificador="1/2020", data_publicacao=None)])

    r = await monitorar_novas_resolucoes(30, caminho_db=caminho_db)

    assert r.total == 0
    assert r.sem_data_publicacao == 1
    assert r.aviso is not None and "sem data de publicação" in r.aviso


def test_suspensao_e_lida_da_marcacao_entre_parenteses() -> None:
    """O CFM cola o status no fim da ementa; é a única pista que ele dá."""
    from radar_cfm_mcp.extract.vigencia import detectar_suspensao

    total = detectar_suspensao("Define a medicina do sono. [RESOLUÇÂO SUSPENSA]")
    assert (total.suspensa, total.parcial) == (True, False)

    parcial = detectar_suspensao("Normas para médicos do trabalho. (PARCIALMENTE SUSPENSA)")
    assert (parcial.suspensa, parcial.parcial) == (True, True)

    judicial = detectar_suspensao(
        "Normas específicas. (Atenção dos dispositivos suspensos por decisão judicial)"
    )
    assert (judicial.suspensa, judicial.parcial) == (True, True)


def test_norma_que_trata_de_suspensao_nao_esta_suspensa() -> None:
    """A 2016/2013 dispõe sobre suspender outra; procurar a palavra solta erraria."""
    from radar_cfm_mcp.extract.vigencia import detectar_suspensao

    ementa = "Dispõe sobre a suspensão da Resolução CRM/DF nº 344/13 e a intervenção eleitoral."
    assert detectar_suspensao(ementa).suspensa is False


@pytest.mark.asyncio
async def test_resposta_marca_suspensa_e_avisa(caminho_db: str) -> None:
    """vigente=true numa norma suspensa é a pior leitura possível para quem aplica."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_registro(ementa="Define a medicina do sono. (RESOLUÇÃO SUSPENSA)")])

    r = await consultar_resolucao_cfm("medicina do sono", caminho_db=caminho_db)

    assert r.resultados[0].vigente is True, "o portal não marcou revogação"
    assert r.resultados[0].suspensa is True
    assert r.resultados[0].nota_vigencia == "RESOLUÇÃO SUSPENSA"
    assert r.aviso is not None and "suspens" in r.aviso.lower()


@pytest.mark.asyncio
async def test_busca_pelo_numero_da_resolucao(caminho_db: str) -> None:
    """Quem digita '2314/2022' quer aquela norma; o índice de texto não indexa número."""
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            [
                _registro(
                    identificador="2314/2022", numero="2314", ano="2022", ementa="telemedicina"
                )
            ],
        )

    for tema in ("2314/2022", "2314", "Resolução CFM nº 2.314/2022"):
        r = await consultar_resolucao_cfm(tema, caminho_db=caminho_db)
        assert r.total == 1, f"não achou com tema={tema!r}"
        assert r.resultados[0].identificador == "2314/2022"


@pytest.mark.asyncio
async def test_busca_por_numero_traz_a_revogada(caminho_db: str) -> None:
    """Pedir pelo número e não receber nada esconde justamente a revogação."""
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            [
                _registro(
                    identificador="1643/2002",
                    numero="1643",
                    ano="2002",
                    vigente=False,
                    revogada_por="2314/2022",
                )
            ],
        )

    r = await consultar_resolucao_cfm("1643/2002", caminho_db=caminho_db, apenas_vigentes=True)

    assert r.total == 1
    assert r.resultados[0].vigente is False
    assert r.aviso is not None and "número" in r.aviso


@pytest.mark.asyncio
async def test_aviso_de_busca_por_palavra_nao_depende_de_truncar(caminho_db: str) -> None:
    """Termo sem sentido trouxe resultado por casar uma palavra, e sem aviso."""
    with conectar(caminho_db) as conexao:
        gravar(conexao, [_registro(ementa="prestação de contas inexistente no exercício")])

    r = await consultar_resolucao_cfm("xyzqwk inexistente", caminho_db=caminho_db)

    assert r.truncado is False
    assert r.aviso is not None and "qualquer palavra do tema" in r.aviso


def test_revogacao_para_alvo_inexistente_e_sinalizada(db: duckdb.DuckDBPyConnection) -> None:
    """O portal erra o ano: a 1643/2002 aponta para '2314/2024', e a 2314 é de 2022."""
    from radar_cfm_mcp.store.queries import anotar_revogacao

    gravar(
        db,
        [
            _registro(
                identificador="1643/2002",
                numero="1643",
                ano="2002",
                vigente=False,
                revogada_por="2314/2024",
            ),
            _registro(identificador="2314/2022", numero="2314", ano="2022"),
        ],
    )

    linhas = anotar_revogacao(db, [{"identificador": "1643/2002", "revogada_por": "2314/2024"}])

    assert linhas[0]["revogada_por_confere"] is False
    assert linhas[0]["revogada_por_provavel"] == "2314/2022"


def test_revogacao_valida_nao_vira_alarme(db: duckdb.DuckDBPyConnection) -> None:
    """Quando o portal acerta, não há nada a sinalizar."""
    from radar_cfm_mcp.store.queries import anotar_revogacao

    gravar(db, [_registro(identificador="2314/2022", numero="2314", ano="2022")])

    linhas = anotar_revogacao(db, [{"identificador": "1/2020", "revogada_por": "2314/2022"}])

    assert linhas[0]["revogada_por_confere"] is True
    assert linhas[0]["revogada_por_provavel"] is None


def test_numero_repetido_em_anos_diferentes_nao_gera_palpite(
    db: duckdb.DuckDBPyConnection,
) -> None:
    """Com dois candidatos, escolher um seria chute apresentado como pista."""
    from radar_cfm_mcp.store.queries import anotar_revogacao

    gravar(
        db,
        [
            _registro(identificador="500/1990", numero="500", ano="1990"),
            _registro(identificador="500/2005", numero="500", ano="2005"),
        ],
    )

    linhas = anotar_revogacao(db, [{"identificador": "1/2020", "revogada_por": "500/2024"}])

    assert linhas[0]["revogada_por_confere"] is False
    assert linhas[0]["revogada_por_provavel"] is None


def test_coleta_sem_pdf_nao_apaga_a_data(db: duckdb.DuckDBPyConnection) -> None:
    """Aconteceu em produção: 1258 datas viraram 5 numa noite."""
    publicada = date(2022, 5, 5)
    gravar(db, [_registro(data_publicacao=publicada, texto_completo="texto do PDF")])
    gravar(db, [_registro(data_publicacao=None, texto_completo=None, ementa="ementa nova")])

    linha = db.execute("SELECT ementa, data_publicacao, texto_completo FROM resolucoes").fetchone()
    assert linha[0] == "ementa nova", "o que a coleta traz tem que entrar"
    assert linha[1] == publicada, "a data veio do PDF e a coleta diária não baixa PDF"
    assert linha[2] == "texto do PDF"


def test_data_nova_substitui_a_antiga(db: duckdb.DuckDBPyConnection) -> None:
    """Preservar não pode virar congelar."""
    gravar(db, [_registro(data_publicacao=date(2020, 1, 1))])
    gravar(db, [_registro(data_publicacao=date(2022, 5, 5))])

    assert db.execute("SELECT data_publicacao FROM resolucoes").fetchone()[0] == date(2022, 5, 5)


def test_data_nos_formatos_que_escapavam() -> None:
    """NOVA-1: cabeçalhos reais que deixavam 903 resoluções sem data."""
    from radar_cfm_mcp.extract.datas import extrair_data_publicacao as extrair

    # 2373/2023, mês abreviado e publicação no ano seguinte
    assert extrair("Publicada no D.O.U. de 15 Jan 2024", 2023) == date(2024, 1, 15)
    # 2304/2022, sem o "de" antes do mês
    assert extrair("(Publicada no D.O.U. de 23 fevereiro de 2022", 2022) == date(2022, 2, 23)
    # 2347/2023, extração do PDF com espaços no meio das palavras
    assert extrair("Publica do e m: 21/09/2023", 2023) == date(2023, 9, 21)
    assert extrair("(Publicada do D.O.U. de 29 set. 2014, Seção I, p. 183)", 2014) == date(
        2014, 9, 29
    )
    assert extrair("(Publicada no Diário Oficial de 21-5-62)", 1962) == date(1962, 5, 21)
    assert extrair("(Publicada no D.O. Seção I — Parte II de 24/1 2/73)", 1973) == date(
        1973, 12, 24
    )
    assert extrair("Publicada no D.O.U. de 1º de agosto de 2011, Seção I", 2011) == date(2011, 8, 1)


def test_data_de_assinatura_na_linha_seguinte_nao_vira_publicacao() -> None:
    from radar_cfm_mcp.extract.datas import extrair_data_publicacao as extrair

    texto = "entrará em vigor na data de sua publicação.\nRio de Janeiro, 12 de abril de 1986"
    assert extrair(texto, 1986) is None


def test_cabecalho_prevalece_sobre_norma_citada_logo_abaixo() -> None:
    """2109/2014 ganhava a data de 2013 da resolução que ela altera."""
    from radar_cfm_mcp.extract.datas import extrair_data_publicacao as extrair

    texto = (
        "RESOLUÇÃO CFM Nº 2.109/2014\n(Publicada no D.O.U. de 31 out. 2014, Seção I)\n"
        "(Resolução CFM nº 2.023/2013, publicada no\nD.O.U. de 28 de agosto de 2013)"
    )
    assert extrair(texto, 2014) == date(2014, 10, 31)


def _no_periodo(n: int, *, ementa: str, dias_atras: int, prefixo: int) -> list[dict[str, Any]]:
    return [
        _registro(
            identificador=f"{prefixo + i}/2020",
            numero=str(prefixo + i),
            ementa=ementa,
            data_publicacao=date.today() - timedelta(days=dias_atras + i),
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_monitor_filtra_o_tema_antes_do_limite(caminho_db: str) -> None:
    """CFM-2: as 50 mais recentes eram de outro tema e as do tema sumiam."""
    with conectar(caminho_db) as conexao:
        gravar(
            conexao,
            _no_periodo(60, ementa="Homologa eleição", dias_atras=1, prefixo=100)
            + _no_periodo(3, ementa="Dispõe sobre telemedicina", dias_atras=400, prefixo=900),
        )

    r = await monitorar_novas_resolucoes(
        3650, caminho_db=caminho_db, palavras_chave=("telemedicina",)
    )

    assert r.total == 3
    assert r.retornados == 3
    assert r.truncado is False
    assert {x.identificador for x in r.resultados} == {"900/2020", "901/2020", "902/2020"}


@pytest.mark.asyncio
async def test_monitor_declara_truncamento(caminho_db: str) -> None:
    with conectar(caminho_db) as conexao:
        gravar(conexao, _no_periodo(63, ementa="Homologa eleição", dias_atras=1, prefixo=100))

    r = await monitorar_novas_resolucoes(365, caminho_db=caminho_db)

    assert (r.total, r.retornados, r.truncado) == (63, 50, True)
    assert r.aviso is not None and "Casaram 63" in r.aviso
    maior = await monitorar_novas_resolucoes(365, caminho_db=caminho_db, limite=100)
    assert (maior.retornados, maior.truncado) == (63, False)


def test_curinga_do_like_no_tema_e_literal(db: duckdb.DuckDBPyConnection) -> None:
    """Sem FTS a busca cai para LIKE, e '%' ou '_' no tema não podem casar tudo."""
    from radar_cfm_mcp.store.queries import contar_por_tema

    gravar(
        db,
        [
            _registro(identificador="1/2020", ementa="Dispõe sobre telemedicina"),
            _registro(identificador="2/2021", numero="2", ano="2021", ementa="Desconto de 100%"),
        ],
    )
    assert buscar_por_tema(db, "_") == []
    assert contar_por_tema(db, "%") == 1
    assert [a["identificador"] for a in buscar_por_tema(db, "100%")] == ["2/2021"]


def test_trecho_marca_o_corte_com_reticencias_ascii() -> None:
    from radar_cfm_mcp.mcp_server.tools.resolucoes import _trecho

    texto = "a" * 400 + " telemedicina " + "b" * 400
    trecho = _trecho(texto, "telemedicina")
    assert trecho is not None
    assert trecho.startswith("...") and trecho.endswith("...")
    assert "…" not in trecho
