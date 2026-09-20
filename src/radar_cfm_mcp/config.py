"""Configuração lida do ambiente (.env)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

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

    @field_validator("palavras_chave", mode="before")
    @classmethod
    def _lista_por_virgula(cls, valor: object) -> object:
        if isinstance(valor, str):
            return tuple(p.strip() for p in valor.split(",") if p.strip())
        return valor


def carregar_config() -> Config:
    return Config()
