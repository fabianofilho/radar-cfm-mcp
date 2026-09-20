# radar-cfm-mcp

Servidor MCP que consulta e monitora resoluções do CFM sobre inteligência artificial,
telemedicina e prontuário eletrônico.

## A fonte: JSON, não scraping

O portal de busca de normas (`portal.cfm.org.br/buscar-normas-cfm-e-crm`) renderiza os
resultados no navegador a partir de uma variável **`resultadoBuscaJson` embutida no
HTML**. O crawler lê esse JSON em vez de raspar tabela — mais estável, e já vem com o
campo `IS_REVOGADA`, que é a informação de vigência que mais importa.

Confirmado em 20/09/2026: **2.457 resoluções**, 10 por página.

Dois detalhes que mudaram o desenho:

- **A busca textual por URL é ignorada** pelo portal (o total não muda com `busca=`), então
  o filtro por palavra-chave é aplicado localmente sobre a ementa.
- **A página de detalhe é um visualizador PDF.js**, não texto. O PDF real fica em
  `sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/{ano}/{numero}_{ano}.pdf`.

## Respeito ao portal

É o site de um conselho profissional, não uma API feita para volume:

- intervalo configurável entre requisições (`CRAWLER_DELAY_SEGUNDOS`, padrão 2s);
- o mesmo PDF nunca é baixado duas vezes (cache por hash da URL);
- **PDF só é baixado quando a ementa casa com alguma palavra-chave** — baixar as 2.457
  seria abusivo, e a ementa já basta para a triagem.

## Rodando

```bash
uv sync
cp .env.example .env
uv run cfm-cli sync --max-paginas 3    # teste rápido
uv run cfm-cli sync                    # varredura completa (~8 min com delay de 2s)
uv run cfm-cli consultar telemedicina
uv run cfm-cli novas --dias 90
uv run radar-cfm-mcp                   # servidor MCP no stdio
```

## Tools

### `consultar_resolucao_cfm(tema, apenas_vigentes=True)`
Resoluções relacionadas ao tema, ordenadas por relevância (BM25 do FTS do DuckDB) e
depois por data. Cada resultado traz ementa, o trecho em volta do termo e **sempre a URL
de origem**. Revogadas vêm com `vigente: false` e o número da que substituiu.

### `monitorar_novas_resolucoes(dias=30, filtrar_tema=True)`
O que foi publicado na janela, filtrado pelos temas configurados.

## Busca: FTS com queda para LIKE

O índice full-text usa a extensão FTS do DuckDB, que é baixada na primeira vez. Se ela
não estiver disponível (máquina offline, por exemplo), a busca cai para `LIKE` e continua
respondendo — mais lenta e sem ranking, mas funcionando.

## Nunca a "posição do CFM" sem a fonte

Toda resposta inclui ementa e link do PDF oficial. Um trecho extraído é um ponto de
partida para leitura, não uma citação normativa.
