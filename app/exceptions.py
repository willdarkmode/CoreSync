class IntegracaoError(Exception):
    """Erro base da integração."""


class ConfiguracaoError(IntegracaoError):
    """Erro de configuração do ambiente."""


class ValidacaoError(IntegracaoError):
    """Erro de validação de dados."""


class WakeAPIError(IntegracaoError):
    """Erro ao consumir a API da Wake."""


class SankhyaAuthError(IntegracaoError):
    """Erro de autenticação com Sankhya."""


class SankhyaAPIError(IntegracaoError):
    """Erro ao consumir a API da Sankhya."""


class IbgeLookupError(IntegracaoError):
    """Erro ao localizar código IBGE."""