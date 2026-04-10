import requests

from app.exceptions import IntegracaoError
from app.utils import somente_digitos


class CnpjLookupError(IntegracaoError):
    """Erro ao consultar dados públicos de CNPJ."""


class CnpjService:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._cache: dict[str, dict | None] = {}

    def buscar_dados_cnpj(self, cnpj: str) -> dict | None:
        cnpj_limpo = somente_digitos(cnpj)

        if not cnpj_limpo:
            return None

        if len(cnpj_limpo) != 14:
            raise CnpjLookupError(f"CNPJ inválido para consulta: {cnpj}")

        if cnpj_limpo in self._cache:
            return self._cache[cnpj_limpo]

        url = f"https://publica.cnpj.ws/cnpj/{cnpj_limpo}"

        try:
            response = requests.get(
                url,
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise CnpjLookupError(
                f"Falha ao buscar dados do CNPJ {cnpj_limpo}: {exc}"
            ) from exc

        estabelecimento = data.get("estabelecimento", {}) or {}
        inscricoes = estabelecimento.get("inscricoes_estaduais") or []

        ie = ""
        if isinstance(inscricoes, list) and inscricoes:
            ativa = next((i for i in inscricoes if i.get("ativo") is True), None)
            if ativa:
                ie = (ativa.get("inscricao_estadual") or "").strip().upper()

        resultado = {
            "razao_social": data.get("razao_social"),
            "nome_fantasia": estabelecimento.get("nome_fantasia"),
            "inscricao_estadual": ie,
        }

        self._cache[cnpj_limpo] = resultado
        return resultado