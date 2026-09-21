# radar-cfm-mcp

Servidor MCP que consulta e monitora resoluções do CFM (Conselho Federal de Medicina)
sobre inteligência artificial, telemedicina e prontuário eletrônico. Mantém uma cópia
local pesquisável e sempre devolve o link do documento oficial.

> ### ⚠️ Não é fonte oficial
>
> - **Não substitui a consulta ao portal do CFM.** Este projeto lê o que o portal publica
>   e guarda uma cópia local, que pode estar defasada.
> - **Um trecho extraído não é "a posição do CFM".** Toda resposta traz a ementa e a URL
>   do PDF oficial justamente para você conferir antes de citar.
> - **A extração de PDF pode falhar ou truncar.** Quando isso acontece o registro é
>   marcado com `metadados_incompletos`, mas leia sempre o documento original.
> - **Sem validação jurídica.** Isto é uma ferramenta de busca, não uma interpretação
>   normativa.

## Requisitos

| O quê | Versão | Para quê |
| --- | --- | --- |
| Python | 3.12+ | runtime |
| [uv](https://docs.astral.sh/uv/) | recente | dependências e venv |
| Espaço em disco | ~200 MB | base DuckDB + PDFs cacheados |

Este projeto **não usa LLM**: a busca é full-text sobre a base local.

## Instalação

```bash
git clone https://github.com/fabianofilho/radar-cfm-mcp.git
cd radar-cfm-mcp
uv sync
cp .env.example .env
```

## Configuração

| Variável | Padrão | Observação |
| --- | --- | --- |
| `CRAWLER_DELAY_SEGUNDOS` | `2` | intervalo entre requisições ao portal |
| `PALAVRAS_CHAVE` | IA, telemedicina, prontuário eletrônico, algoritmo | separadas por vírgula |
| `DUCKDB_PATH` | `./data/cfm.duckdb` | base local |
| `SYNC_HORA_LOCAL` | `02:00` | horário fixo do sync agendado |

```bash
uv run cfm-cli sync --max-paginas 3    # teste rápido
uv run cfm-cli sync                    # varredura completa (~8 min com delay de 2s)
uv run cfm-cli consultar telemedicina
uv run cfm-cli novas --dias 90
uv run cfm-cli schema
```

### Ligando ao Claude Code

```bash
claude mcp add radar-cfm --scope user \
  -e DUCKDB_PATH=/caminho/para/radar-cfm-mcp/data/cfm.duckdb \
  -- uv --directory /caminho/para/radar-cfm-mcp run radar-cfm-mcp
```

## Uso

### `consultar_resolucao_cfm(tema: str, apenas_vigentes=True)`

```json
{
  "tema": "inteligência artificial",
  "total": 1,
  "resultados": [
    {
      "identificador": "2454/2026",
      "ementa": "Normatiza o uso da inteligência artificial na medicina.",
      "vigente": true,
      "revogada_por": null,
      "trecho_relevante": "…Normatiza o uso da inteligência artificial na medicina. O CONSELHO FEDERAL DE MEDICINA…",
      "url_origem": "https://sistemas.cfm.org.br/normas/visualizar/resolucoes/BR/2026/2454",
      "texto_completo_disponivel": true
    }
  ]
}
```

Ordena por relevância (BM25 do FTS do DuckDB) e depois por data. Revogadas vêm com
`vigente: false` e o número da que substituiu.

### `monitorar_novas_resolucoes(dias=30, filtrar_tema=True)`

O que foi publicado na janela, filtrado pelos temas configurados.

## Como a fonte funciona

O portal de busca de normas renderiza os resultados no navegador a partir de uma variável
**`resultadoBuscaJson` embutida no HTML**. O crawler lê esse JSON em vez de raspar tabela:
é mais estável, e já vem com o campo de revogação.

Confirmado em 20/09/2026: **2.457 resoluções**, 10 por página, 246 páginas.

Dois detalhes que moldaram o desenho:

- **A busca textual por URL é ignorada** pelo portal (o total não muda com `busca=`), então
  o filtro por palavra-chave é aplicado localmente sobre a ementa.
- **A página de detalhe é um visualizador PDF.js**, não texto. O PDF real segue o padrão
  `sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/{ano}/{numero}_{ano}.pdf`.

## Respeito ao portal

É o site de um conselho profissional, não uma API feita para volume:

- intervalo configurável entre requisições (padrão 2s);
- o mesmo PDF nunca é baixado duas vezes (cache por hash da URL);
- **PDF só é baixado quando a ementa casa com alguma palavra-chave.** Baixar as 2.457
  seria abusivo, e a ementa já basta para a triagem.

## Limitações conhecidas

**O corpus relevante é pequeno, e isso é do CFM, não do projeto.** Das 2.457 resoluções,
apenas **5 ementas** mencionam os temas padrão: a 2.454/2026 (IA), a 2.314/2022, as
2.227/2018 e 2.228/2019 (revogadas) e a 1.643/2002. Se você espera dezenas de resultados,
o problema é a expectativa.

**O filtro age sobre a ementa, não sobre o texto completo.** Uma resolução que trate de IA
no corpo sem dizer isso na ementa não terá o PDF baixado. Ajuste `PALAVRAS_CHAVE` se o seu
tema for outro.

**A vigência vem do portal, não de análise jurídica.** O campo reflete o que o CFM marca
como revogado. Revogação parcial ou alteração por outra resolução não aparece como tal.

**O parser depende do formato da página.** Se o portal mudar, o crawler falha com uma
mensagem explícita (`resultadoBuscaJson não encontrado`) em vez de devolver vazio em
silêncio — mas vai falhar.

**A busca cai para `LIKE` sem o FTS.** A extensão full-text do DuckDB é baixada na primeira
execução. Sem rede, a busca continua respondendo, mais lenta e sem ranking.

## Privacidade

- **Sai da máquina:** requisições ao `portal.cfm.org.br` e ao `sistemas.cfm.org.br`, para
  a busca e os PDFs públicos.
- **Não sai:** os temas que você consulta. A busca roda contra a base local.
- Sem LLM, sem telemetria, sem analytics.

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md). Não rode o crawler em loop nem reduza o delay.

## Licença e atribuição

[Apache License 2.0](LICENSE) — escolhida por o projeto tocar em regulação de conduta
médica.

Construído no contexto do [IA.med](https://iamed.cc).
