"""Configuração lida do ambiente (.env)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    crawler_delay_segundos: float = Field(default=2.0, ge=0.5)
    # NoDecode: sem ele o pydantic-settings tenta ler o valor do .env como JSON
    # antes do validador rodar, e uma lista separada por vírgula estoura.
    palavras_chave: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("inteligência artificial", "telemedicina", "prontuário eletrônico", "algoritmo")
    )
    duckdb_path: Path = Field(default=Path("./data/cfm.duckdb"))
    # Horário fixo, não intervalo: os syncs locais são escalonados de madrugada.
    sync_hora_local: str = Field(default="02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    log_level: str = Field(default="INFO")

    # --- modo connector (servidor HTTP publico) ---
    # Por padrao o servidor fala stdio: o cliente sobe o processo na maquina de
    # quem usa. Em "streamable-http" ele vira um servidor alcancavel pela rede,
    # que e o que o Claude aceita como custom connector.
    transporte: Literal["stdio", "streamable-http"] = Field(default="stdio")
    http_host: str = Field(default="127.0.0.1")
    http_porta: int = Field(default=8000, ge=1, le=65535)
    http_path: str = Field(default="/mcp")
    # Stateless: cada requisicao e independente, sem sessao guardada no servidor.
    # Escala melhor e simplifica o deploy; para duas tools de consulta, basta.
    http_stateless: bool = Field(default=True)
    # Dois tetos por minuto. O global e o que protege a maquina: quando o
    # Claude.ai chama um connector, as requisicoes chegam dos IPs da Anthropic,
    # entao limitar por IP colocaria todos os usuarios no mesmo balde. O por
    # origem serve contra quem chama o servidor direto, fora do Claude.
    http_limite_global_por_minuto: int = Field(default=1200, ge=1)
    http_limite_por_minuto: int = Field(default=600, ge=1)
    # Nomes pelos quais o servidor aceita ser chamado, separados por virgula.
    # Vazio significa so loopback. O SDK valida o cabecalho Host contra esta
    # lista e responde 421 fora dela: e defesa contra DNS rebinding, onde um
    # site qualquer faz o navegador da vitima falar com um servidor local. Atras
    # de um proxy ou tunel, o Host que chega e o nome publico, entao ele precisa
    # constar aqui; a alternativa seria desligar a checagem, que e pior.
    http_hosts_publicos: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("http_hosts_publicos", mode="before")
    @classmethod
    def _dividir_hosts(cls, valor: object) -> object:
        """Aceita "a.exemplo,b.exemplo" do .env, nao so lista JSON."""
        if isinstance(valor, str):
            return [p.strip() for p in valor.split(",") if p.strip()]
        return valor

    @field_validator("palavras_chave", mode="before")
    @classmethod
    def _lista_por_virgula(cls, valor: object) -> object:
        if isinstance(valor, str):
            return tuple(p.strip() for p in valor.split(",") if p.strip())
        return valor


def carregar_config() -> Config:
    return Config()
