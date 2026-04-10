import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "t", "yes", "y", "sim", "s"}


@dataclass(frozen=True)
class Settings:
    wake_auth: str
    wake_base_url: str

    sankhya_x_token: str
    sankhya_client_id: str
    sankhya_client_secret: str
    sankhya_base_url: str

    codigo_vendedor: int
    tipo_pagamento_padrao: int
    codigo_local_estoque: int
    nota_modelo: int
    unidade_padrao: str

    permitir_envio: bool
    log_level: str
    timeout_padrao: int
    zerar_ipi_itens: bool


def get_settings() -> Settings:
    return Settings(
        wake_auth=os.getenv("WAKE_AUTH", "").strip(),
        wake_base_url=os.getenv("WAKE_BASE_URL", "https://api.fbits.net").strip(),

        sankhya_x_token=os.getenv("SANKHYA_X_TOKEN", "").strip(),
        sankhya_client_id=os.getenv("SANKHYA_CLIENT_ID", "").strip(),
        sankhya_client_secret=os.getenv("SANKHYA_CLIENT_SECRET", "").strip(),
        sankhya_base_url=os.getenv("SANKHYA_BASE_URL", "https://api.sankhya.com.br").strip(),

        codigo_vendedor=int(os.getenv("CODIGO_VENDEDOR", "28")),
        tipo_pagamento_padrao=int(os.getenv("TIPO_PAGAMENTO_PADRAO", "11")),
        codigo_local_estoque=int(os.getenv("CODIGO_LOCAL_ESTOQUE", "10100")),
        nota_modelo=int(os.getenv("NOTA_MODELO", "92380")),
        unidade_padrao=os.getenv("UNIDADE_PADRAO", "UN").strip(),

        permitir_envio=_get_bool("PERMITIR_ENVIO", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        timeout_padrao=int(os.getenv("TIMEOUT_PADRAO", "30")),
        zerar_ipi_itens=_get_bool("ZERAR_IPI_ITENS", False),
    )