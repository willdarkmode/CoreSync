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
    codigo_cliente_fiscal_referencia: int
    codigo_empresa: int
    codigo_vendedor: int
    tipo_pagamento_padrao: int
    codigo_local_estoque: int
    nota_modelo: int
    unidade_padrao: str
    permitir_envio: bool
    log_level: str
    timeout_padrao: int
    idempotency_enabled: bool
    firebase_credentials_path: str
    firebase_project_id: str
    idempotency_collection: str
    cnpja_api_key: str
    ipi_strategy: str


def get_settings() -> Settings:
    return Settings(
        wake_auth=os.getenv("WAKE_AUTH", "").strip(),
        wake_base_url=os.getenv("WAKE_BASE_URL", "https://api.fbits.net").strip(),
        sankhya_x_token=os.getenv("SANKHYA_X_TOKEN", "").strip(),
        sankhya_client_id=os.getenv("SANKHYA_CLIENT_ID", "").strip(),
        sankhya_client_secret=os.getenv("SANKHYA_CLIENT_SECRET", "").strip(),
        sankhya_base_url=os.getenv("SANKHYA_BASE_URL", "https://api.sankhya.com.br").strip(),
        codigo_cliente_fiscal_referencia=int(os.getenv("CODIGO_CLIENTE_FISCAL_REFERENCIA", "10570")),
        codigo_empresa=int(os.getenv("CODIGO_EMPRESA", "1")),
        codigo_vendedor=int(os.getenv("CODIGO_VENDEDOR", "28")),
        tipo_pagamento_padrao=int(os.getenv("TIPO_PAGAMENTO_PADRAO", "11")),
        codigo_local_estoque=int(os.getenv("CODIGO_LOCAL_ESTOQUE", "10100")),
        nota_modelo=int(os.getenv("NOTA_MODELO", "92380")),
        unidade_padrao=os.getenv("UNIDADE_PADRAO", "UN").strip(),
        permitir_envio=_get_bool("PERMITIR_ENVIO", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        timeout_padrao=int(os.getenv("TIMEOUT_PADRAO", "30")),
        idempotency_enabled=_get_bool("IDEMPOTENCY_ENABLED", True),
        firebase_credentials_path=os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip(),
        firebase_project_id=os.getenv("FIREBASE_PROJECT_ID", "").strip(),
        idempotency_collection=os.getenv("IDEMPOTENCY_COLLECTION", "pedidos_integrados").strip(),
        cnpja_api_key=os.getenv("CNPJA_API_KEY", "").strip(),
        ipi_strategy=os.getenv("IPI_STRATEGY", "discount_compensation").strip().lower(),
    )

def validar_config(settings: Settings) -> None:
    if not settings.wake_auth:
        raise ValueError("WAKE_AUTH não configurado")

    if not settings.sankhya_x_token:
        raise ValueError("SANKHYA_X_TOKEN não configurado")

    if not settings.sankhya_client_id:
        raise ValueError("SANKHYA_CLIENT_ID não configurado")

    if not settings.sankhya_client_secret:
        raise ValueError("SANKHYA_CLIENT_SECRET não configurado")

    if settings.idempotency_enabled:
        if not settings.firebase_credentials_path:
            raise ValueError("FIREBASE_CREDENTIALS_PATH não configurado")

        if not settings.firebase_project_id:
            raise ValueError("FIREBASE_PROJECT_ID não configurado")

        if not settings.idempotency_collection:
            raise ValueError("IDEMPOTENCY_COLLECTION não configurado")