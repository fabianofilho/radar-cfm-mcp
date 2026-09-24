# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O
projeto segue versionamento semântico.

## [0.1.0] - 2026-09-24

Primeira versão publicada.

### O que há

- Duas tools MCP:
  - `consultar_resolucao_cfm(tema, apenas_vigentes=True, limite=10)`: busca full-text
    (FTS do DuckDB, com queda para `LIKE`) sobre ementa e texto integral, com trecho
    relevante, vigência, suspensão lida da ementa, revogação conferida contra a base e
    busca direta pelo número da resolução.
  - `monitorar_novas_resolucoes(dias=30, filtrar_tema=True, limite=50)`: resoluções
    publicadas na janela, com `total`, `retornados`, `truncado` e
    `sem_data_publicacao`.
- Crawler do portal de normas do CFM, que lê o JSON embutido na página de busca, com
  intervalo entre requisições e cache de PDFs em disco.
- Base DuckDB publicada por troca atômica (`cfm-cli sync --publicar`), que parte de uma
  cópia da servida e recusa publicar base que encolheu mais de 10%.
- Modo connector (`TRANSPORTE=streamable-http`) com teto de requisições global e por
  origem, validação de `Host` para uso atrás de proxy ou túnel, e tetos nos parâmetros
  das tools (`limite` até 100, `dias` até 3.650).
- Units systemd de usuário em `deploy/`: connector, coleta diária e timer.
- Connector público em `https://mcp.tailf42a96.ts.net/cfm/mcp`, sem garantia de
  disponibilidade.
- CLI `cfm-cli`: `sync`, `reextrair-datas`, `consultar`, `novas`, `schema`.

### O que mudou nesta finalização

- O monitoramento filtra as palavras-chave no SQL, antes do limite. Antes, o corte em
  50 vinha primeiro e, numa janela longa, as resoluções do tema sumiam sem aviso. A
  resposta ganhou `total`, `retornados` e `truncado`, e a tool ganhou `limite`.
- O extrator de data de publicação passou a ler mês abreviado, mês sem o primeiro
  "de" (inclusive "03 dezembro de 2013"), texto com espaços quebrados, "D.O." sem o U e datas com hífen. Numa cópia da
  base, 856 das 1.199 resoluções sem data ganharam data e 14 datas erradas (de outra
  norma citada no cabeçalho) foram corrigidas.
- O sync recalcula a data a partir do texto já gravado ao fim de cada coleta, e o
  comando `cfm-cli reextrair-datas` faz o mesmo sem tocar no portal.
- A coleta diária roda com `--texto-integral --max-pdfs 25`, para que toda resolução
  nova tenha texto e data. Resolução que já tem texto na base não volta ao parser.
- A busca por `LIKE` escapa `%` e `_` do tema.
- Tetos em `limite` e `dias` e leitura confiável do `X-Forwarded-For` no limitador
  (só atrás de proxy em loopback, usando a entrada mais à direita).
- Saiu o agendador interno (APScheduler, `SYNC_HORA_LOCAL`): o timer systemd é o
  caminho oficial.
- `mcp>=2.2,<3` no lugar de `mcp>=1.2`, que não tinha o módulo usado pelo servidor.
- Licença declarada no `pyproject.toml`, `SECURITY.md`, `.claude/` no `.gitignore`.
