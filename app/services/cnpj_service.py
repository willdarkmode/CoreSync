import requests

from app.exceptions import IntegracaoError
from app.utils import somente_digitos


class CnpjLookupError(IntegracaoError):
    """Erro ao consultar dados públicos de CNPJ."""


class CnpjService:
    def __init__(
        self,
        timeout: int = 20,
        cnpja_api_key: str | None = None,
    ):
        self.timeout = timeout
        self.cnpja_api_key = cnpja_api_key
        self._cache: dict[str, dict | None] = {}

    def buscar_dados_cnpj(self, cnpj: str, uf: str | None = None) -> dict | None:
        cnpj_limpo = somente_digitos(cnpj)

        if not cnpj_limpo:
            return None

        if len(cnpj_limpo) != 14:
            raise CnpjLookupError(f"CNPJ inválido para consulta: {cnpj}")

        uf_normalizada = (uf or "").strip().upper()
        cache_key = f"{cnpj_limpo}:{uf_normalizada}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        resultado = self._buscar_cnpjws(cnpj_limpo, uf_normalizada)

        if resultado and resultado.get("inscricao_estadual"):
            self._cache[cache_key] = resultado
            return resultado

        resultado_fallback = self._buscar_cnpja(cnpj_limpo, uf_normalizada)

        if resultado_fallback and resultado_fallback.get("inscricao_estadual"):
            if resultado:
                resultado["inscricao_estadual"] = resultado_fallback["inscricao_estadual"]
                resultado["fonte_ie"] = resultado_fallback.get("fonte_ie") or "cnpja"
            else:
                resultado = resultado_fallback

        self._cache[cache_key] = resultado
        return resultado

    def _buscar_cnpjws(self, cnpj_limpo: str, uf: str | None = None) -> dict | None:
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

        uf = (uf or "").strip().upper()
        ie = ""

        if isinstance(inscricoes, list) and inscricoes:
            if uf:
                ativa = next(
                    (
                        i for i in inscricoes
                        if (
                            i.get("ativo") is True
                            and (
                                (i.get("estado", {}) or {}).get("sigla")
                                or i.get("uf")
                                or i.get("estado_sigla")
                                or ""
                            ).upper() == uf
                        )
                    ),
                    None,
                )
            else:
                ativa = next(
                    (i for i in inscricoes if i.get("ativo") is True),
                    None,
                )

            if ativa:
                ie = somente_digitos(ativa.get("inscricao_estadual") or "")

        return {
            "razao_social": data.get("razao_social"),
            "nome_fantasia": estabelecimento.get("nome_fantasia"),
            "inscricao_estadual": ie,
            "fonte_ie": "cnpjws" if ie else "",
        }

    def _buscar_cnpja(self, cnpj_limpo: str, uf: str | None = None) -> dict | None:
        if not self.cnpja_api_key:
            return None

        url = f"https://api.cnpja.com/office/{cnpj_limpo}"

        try:
            response = requests.get(
                url,
                params={
                    "registrations": "ORIGIN",
                    "strategy": "CACHE_IF_ERROR",
                },
                headers={
                    "Accept": "application/json",
                    "Authorization": self.cnpja_api_key,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            return None

        inscricoes = data.get("registrations") or []

        uf = (uf or "").strip().upper()
        ie = ""

        if isinstance(inscricoes, list) and inscricoes:
            if uf:
                ativa = next(
                    (
                        i for i in inscricoes
                        if (
                            i.get("enabled") is True
                            and i.get("number")
                            and (i.get("state") or "").upper() == uf
                        )
                    ),
                    None,
                )
            else:
                ativa = next(
                    (
                        i for i in inscricoes
                        if i.get("enabled") is True and i.get("number")
                    ),
                    None,
                )

            if ativa:
                ie = somente_digitos(ativa.get("number") or "")

        if not ie:
            return None

        return {
            "razao_social": data.get("company", {}).get("name"),
            "nome_fantasia": data.get("alias"),
            "inscricao_estadual": ie,
            "fonte_ie": "cnpja",
        }