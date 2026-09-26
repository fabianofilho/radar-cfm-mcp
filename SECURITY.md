# Segurança

## Como reportar

Não descreva vulnerabilidade em issue pública. Use o reporte privado do GitHub
(aba **Security**, "Report a vulnerability") neste repositório. Se a opção não
aparecer, abra uma issue pedindo um canal privado, sem detalhes da falha.

Descreva o que é possível fazer, os passos para reproduzir e a versão ou commit. O
projeto é mantido por uma pessoa; a resposta costuma vir em alguns dias, sem prazo
garantido.

## Escopo

Entra:

- o código deste repositório (servidor MCP, crawler, CLI, units em `deploy/`);
- o connector público em `https://debian-f.tailf42a96.ts.net/cfm/mcp`: contornar os tetos
  de requisição ou de parâmetros, ler ou alterar arquivos fora da base, derrubar o
  serviço com poucas requisições.

Não entra:

- o conteúdo das resoluções e o portal do CFM, que são de terceiros;
- ataque distribuído de negação de serviço ao connector público, que não tem garantia
  de disponibilidade;
- a ausência de autenticação no connector público, que é intencional: ele serve só
  dados públicos e é somente leitura.
