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

## Connector hospedado

Há uma instância pública deste servidor, mantida pelo autor, para quem quer usar sem
instalar nada:

```
https://mcp.tailf42a96.ts.net/cfm/mcp
```

- **No Claude (web ou desktop):** Configurações, Conectores, adicionar conector
  personalizado, e colar a URL acima.
- **No Claude Code:** `claude mcp add --transport http radar-cfm https://mcp.tailf42a96.ts.net/cfm/mcp`

Antes de usar, saiba o que ela é:

- **Os dados são públicos, do CFM.** A base é uma cópia do que o portal de normas
  publica, atualizada uma vez por dia de madrugada. O aviso acima vale inteiro: não é
  fonte oficial.
- **Sem garantia de disponibilidade.** Roda numa máquina pessoal exposta pelo Tailscale
  Funnel. Pode ficar fora do ar, mudar de endereço ou ser desligada sem aviso. Para uso
  de que você dependa, rode a sua (instruções abaixo).
- **Sem autenticação, com limites.** Qualquer pessoa pode chamar. Por isso há teto de
  requisições (600 por minuto por origem e 1.200 por minuto no total) e teto nos
  parâmetros (`limite` até 100, `dias` até 3.650). Acima disso a chamada é recusada.
- **O que você consulta passa por essa máquina.** Veja [Privacidade](#privacidade).

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
| `PALAVRAS_CHAVE` | inteligência artificial, telemedicina, prontuário eletrônico, algoritmo | separadas por vírgula; decidem quais PDFs o sync padrão baixa (pela ementa) e o filtro de `monitorar_novas_resolucoes` (ementa e texto) |
| `DUCKDB_PATH` | `./data/cfm.duckdb` | base local |

```bash
uv run cfm-cli sync --max-paginas 3    # teste rápido
uv run cfm-cli sync                    # varredura completa (~8 min com delay de 2s)
uv run cfm-cli consultar telemedicina
uv run cfm-cli novas --dias 90
uv run cfm-cli reextrair-datas         # refaz as datas a partir do texto já gravado
uv run cfm-cli schema
```

### Ligando ao Claude Code

```bash
claude mcp add radar-cfm --scope user \
  -e DUCKDB_PATH=/caminho/para/radar-cfm-mcp/data/cfm.duckdb \
  -- uv --directory /caminho/para/radar-cfm-mcp run radar-cfm-mcp
```

## Uso

### `consultar_resolucao_cfm(tema: str, apenas_vigentes=True, limite=10)`

`limite` vai de 1 a 100. Um tema que seja o número de uma resolução (`2314/2022`,
`2.314`) busca aquela norma diretamente, inclusive revogada.

```json
{
  "tema": "inteligência artificial",
  "total": 1,
  "retornados": 1,
  "truncado": false,
  "resultados": [
    {
      "identificador": "2454/2026",
      "ementa": "Normatiza o uso da inteligência artificial na medicina.",
      "vigente": true,
      "revogada_por": null,
      "trecho_relevante": "...Normatiza o uso da inteligência artificial na medicina. O CONSELHO FEDERAL DE MEDICINA...",
      "url_origem": "https://sistemas.cfm.org.br/normas/visualizar/resolucoes/BR/2026/2454",
      "texto_completo_disponivel": true
    }
  ]
}
```

Ordena por relevância (BM25 do FTS do DuckDB) e depois por data. Revogadas vêm com
`vigente: false` e o número da que substituiu. `total` é quantas casam na base
inteira; `truncado: true` quer dizer que há mais do que veio.

### `monitorar_novas_resolucoes(dias=30, filtrar_tema=True, limite=50)`

O que foi publicado nos últimos `dias` (1 a 3.650), mais recentes primeiro, até
`limite` (1 a 100). Com `filtrar_tema`, só as que citam alguma das `PALAVRAS_CHAVE`
na ementa ou no texto; o filtro é aplicado antes do limite. A resposta traz `total`,
`retornados`, `truncado` e `sem_data_publicacao`: a janela usa a data extraída do
texto, e as resoluções sem data ficam de fora, então total zero com muitas sem data
quer dizer "não sei", não "nada foi publicado".

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
- **Por padrão, o PDF só é baixado quando a ementa casa com alguma palavra-chave.** A
  instância hospedada baixou as 2.457 uma vez, e a coleta diária dela só baixa o que é
  novo (ver "Texto integral" abaixo).

## Modo connector (servidor HTTP)

Por padrão o servidor fala **stdio**: o cliente sobe o processo na máquina de quem usa.
Com `TRANSPORTE=streamable-http`, ele vira um servidor alcançável pela rede, que é o que
o Claude aceita como custom connector.

```bash
TRANSPORTE=streamable-http HTTP_HOST=0.0.0.0 HTTP_PORTA=8000 uv run radar-cfm-mcp
```

| Variável | Padrão | Observação |
| --- | --- | --- |
| `TRANSPORTE` | `stdio` | `streamable-http` liga o modo connector |
| `HTTP_HOST` / `HTTP_PORTA` / `HTTP_PATH` | `127.0.0.1` / `8000` / `/mcp` | |
| `HTTP_STATELESS` | `true` | cada requisição independente; escala melhor |
| `HTTP_LIMITE_GLOBAL_POR_MINUTO` | `1200` | o teto que protege a máquina |
| `HTTP_LIMITE_POR_MINUTO` | `600` | por origem, contra chamada direta |

**Por que dois limites.** Quando o Claude chama um connector remoto, as requisições chegam
dos **IPs da Anthropic**, não do usuário final. Limitar só por IP colocaria todos os
usuários no mesmo balde: ou derruba todo mundo junto, ou não protege nada. O teto global é
o que vale para esse tráfego; o por origem serve contra quem chama o servidor direto.

### Atrás de um proxy ou túnel, declare o nome público

O SDK do MCP valida o cabeçalho `Host` e responde **421 Invalid Host** ao que não
reconhece. É proteção contra DNS rebinding, um ataque em que um site qualquer faz o
navegador da vítima conversar com um servidor que só deveria ser local.

Quando o servidor fica atrás de um túnel, o `Host` que chega é o nome público, não
`127.0.0.1`, e toda requisição legítima leva 421. A saída certa é declarar o nome, não
desligar a checagem:

```bash
HTTP_HOSTS_PUBLICOS=mcp.exemplo.ts.net
```

Aceita vários separados por vírgula. O loopback continua valendo junto, porque é assim
que se testa o servidor de dentro da máquina.

### Texto integral: baixar o PDF de todas

Por padrão o sync baixa o PDF só das resoluções cuja ementa toca em IA ou
telemedicina, que é o escopo do projeto. São 5 das 2.457. Para as outras, a busca por
tema compara apenas a ementa, e `trecho_relevante` vem nulo: não porque a norma não
trate do assunto, mas porque o texto nunca foi lido. A resposta diz isso, em
`texto_completo_disponivel` e no aviso.

Para servir a base a mais gente, vale baixar tudo uma vez:

```bash
uv run cfm-cli sync --texto-integral --max-pdfs 0 --publicar
```

Com o intervalo padrão de 2s entre requisições, a primeira execução leva perto de uma
hora e meia e ocupa cerca de 250 MB em `data/pdfs`. O cache em disco evita repetir, e
resolução que já tem texto na base nem volta ao parser: as execuções seguintes só
baixam o que é novo.

A coleta diária de `deploy/radar-cfm-sync.service` roda com `--texto-integral
--max-pdfs 25`. Sem isso, uma resolução nova fora de IA e telemedicina entraria sem
texto e, portanto, sem data de publicação, e nunca apareceria no monitoramento.

**A coleta seguinte não apaga o texto.** Quem roda o sync diário sem `--texto-integral`
não traz PDF nenhum, e um upsert comum sobrescreveria as extrações com nulo. A coluna
só é substituída quando o novo valor tem conteúdo, porque texto ausente na coleta
significa "não busquei desta vez", nunca "a norma ficou sem texto".

### Rodar como serviço

`deploy/` tem as units de usuário do systemd que rodam a instância hospedada. Elas
assumem o repositório em `~/radar-cfm-mcp` e o `uv` em `~/.local/bin/uv`; ajuste os
caminhos se o seu for outro.

| Unit | O que faz |
| --- | --- |
| `radar-cfm-connector.service` | o servidor HTTP (modo connector), só leitura |
| `radar-cfm-sync.service` | uma coleta com `--publicar --texto-integral --max-pdfs 25` |
| `radar-cfm-sync.timer` | dispara a coleta todo dia às 02:00, com até 30 min de espalhamento |

```bash
cp deploy/radar-cfm-connector.service deploy/radar-cfm-sync.service \
   deploy/radar-cfm-sync.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now radar-cfm-connector.service radar-cfm-sync.timer
```

A unit do connector vem com `HTTP_HOSTS_PUBLICOS=` vazio. Preencha com o nome público
do túnel ou proxy antes de habilitar, senão tudo que chega de fora recebe 421. Para
atualizar só a coleta numa máquina que já roda o connector, copie apenas
`radar-cfm-sync.service` e `radar-cfm-sync.timer` e rode `systemctl --user
daemon-reload`; assim o nome público já configurado no connector não se perde.

O timer do systemd é o único agendador: o projeto não tem agendador interno. Para
rodar uma coleta fora do horário, `systemctl --user start radar-cfm-sync.service`, e o
resultado sai em `journalctl --user -u radar-cfm-sync`.

Ela **escuta só em 127.0.0.1**. Expor para fora é uma camada à parte, um proxy reverso
com TLS ou um túnel, que aponta para essa porta. Manter assim deixa a decisão de expor
num lugar só, em vez de espalhada em variável de ambiente.

O serviço só lê. Quem escreve é a coleta, que roda separada e troca o arquivo por rename.

Se o processo morrer, o systemd sobe de novo em 5 segundos (`Restart=always`).

### A base não vai junto, e o sync roda fora

O DuckDB recusa abrir para escrita enquanto houver um leitor, e no modo connector o
servidor abre a base a cada requisição. Escrever direto no arquivo servido falharia sempre
que a coleta caísse em cima de uma consulta.

Por isso o sync usa `--publicar`: constrói a base ao lado e troca por `os.replace`, que é
atômico no POSIX. Quem já abriu continua no arquivo antigo até fechar (o tempo de uma
requisição); quem abrir depois pega o novo.

```bash
uv run cfm-cli sync --publicar          # constrói ao lado e troca no fim
uv run cfm-cli sync --publicar --forcar # aceita base menor que a servida
```

**A base ao lado começa como cópia da servida, não vazia.** Uma varredura que pare no meio
(rede ruim, portal fora) produziria uma base com só uma parte das resoluções, e publicá-la
tiraria as outras do ar. Com a cópia, a coleta faz upsert por cima do que já existe: o pior
caso é uma base desatualizada, nunca uma base menor. A cópia é feita pelo próprio DuckDB
(`COPY FROM DATABASE`), porque um `cp` pegaria o arquivo sem o WAL pendente.

**A publicação é recusada quando a base nova encolhe mais de 10%.** Com a cópia acima, uma
varredura interrompida já não produz base pequena: ela só deixa de atualizar. A checagem
fica como rede de segurança para o que a cópia não cobre, como um clone que falhou pela
metade. A versão trocada fica como `.anterior`, e
`store.troca.reverter()` volta atrás.

### Hospedar reduz a carga no CFM

Hoje, cada pessoa que clona o repositório roda o próprio crawler nas 246 páginas. Com um
connector, uma instância varre e todo mundo consulta a mesma base. Para um portal de
conselho profissional, o connector é a opção mais respeitosa.

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
silêncio, mas vai falhar.

**Nem toda resolução tem data de publicação.** A data sai do cabeçalho do texto, e o
campo de data do portal não serve (é a data de carga no sistema deles). Com o extrator
desta versão, medido em 24/09/2026 sobre uma cópia da base, 343 das 2.457 continuam sem
data, quase todas antigas cujo PDF traz o cabeçalho sem a data ("Publicada no D.O. Seção
I, Parte II de", e mais nada). Essas não entram em `monitorar_novas_resolucoes`, que
informa quantas são em `sem_data_publicacao`.

**O portal erra o ano em `revogada_por`.** A Resolução 1.643/2002 vem como revogada pela
"2314/2024", e a 2.314 é de 2022. O valor fica como o CFM publica, mas a resposta marca
`revogada_por_confere: false` e sugere a provável em `revogada_por_provavel`, que é
inferência nossa.

**A busca cai para `LIKE` sem o FTS.** A extensão full-text do DuckDB é baixada na primeira
execução. Sem rede, a busca continua respondendo, mais lenta e sem ranking.

## Privacidade

**Rodando localmente (stdio):**

- **Sai da máquina:** requisições ao `portal.cfm.org.br` e ao `sistemas.cfm.org.br`, para
  a busca e os PDFs públicos, só durante o sync.
- **Não sai:** os temas que você consulta. A busca roda contra a base local.

**Usando o connector hospedado:**

- **Sai da sua máquina:** os parâmetros de cada chamada (tema, dias, limite) vão para o
  servidor do autor, passando pelo Tailscale Funnel.
- **O que o servidor guarda:** o código deste projeto não registra os temas consultados.
  O único registro por requisição é o IP de origem quando uma chamada é recusada por
  limite de taxa.

Nos dois modos: sem LLM, sem telemetria, sem analytics.

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md). Não rode o crawler em loop nem reduza o delay.

## Licença e atribuição

[Apache License 2.0](LICENSE): escolhida por o projeto tocar em regulação de conduta
médica.

Construído no contexto do [IA.med](https://iamed.cc).
