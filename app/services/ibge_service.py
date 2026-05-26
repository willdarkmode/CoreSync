import re
import requests
import unicodedata
from app.exceptions import IbgeLookupError
from app.utils import normalizar_texto


class IbgeService:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._cache: dict[tuple[str, str], int] = {}
        self._cep_cache: dict[str, dict] = {}

    def remover_acentos(self, texto: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", texto or "")
            if unicodedata.category(c) != "Mn"
        )

    def _normalizar_cidade_ibge(self, cidade: str) -> str:
        cidade = normalizar_texto(cidade or "")
        cidade = self.remover_acentos(cidade)
        cidade = re.sub(r"[^a-z0-9]+", " ", cidade)
        cidade = re.sub(r"\s+", " ", cidade).strip()
        return cidade

    def _normalizar_cep(self, cep: str) -> str:
        return re.sub(r"\D", "", cep or "")

    def obter_municipio_por_cep(self, cep: str) -> dict | None:
        cep = self._normalizar_cep(cep)

        if len(cep) != 8:
            return None

        if cep in self._cep_cache:
            return self._cep_cache[cep]

        url = f"https://viacep.com.br/ws/{cep}/json/"

        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            return None

        if data.get("erro"):
            return None

        resultado = {
            "cidade": data.get("localidade"),
            "uf": data.get("uf"),
            "codigo_ibge": int(data["ibge"]) if data.get("ibge") else None,
            "bairro": data.get("bairro"),
            "logradouro": data.get("logradouro"),
        }

        self._cep_cache[cep] = resultado
        return resultado

    def obter_codigo_ibge(self, cidade: str, uf: str, cep: str | None = None) -> int:
        if not cidade or not uf:
            if cep:
                municipio = self.obter_municipio_por_cep(cep)
                if municipio and municipio.get("codigo_ibge"):
                    return municipio["codigo_ibge"]

            raise IbgeLookupError("Cidade/UF ausentes para busca do código IBGE.")

        chave = (self._normalizar_cidade_ibge(cidade), uf.strip().upper())

        if chave in self._cache:
            return self._cache[chave]

        url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{chave[1]}/municipios"

        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            municipios = resp.json()
        except requests.RequestException as exc:
            raise IbgeLookupError(f"Erro ao consultar IBGE: {exc}") from exc

        cidade_norm = chave[0]

        for municipio in municipios:
            nome = self._normalizar_cidade_ibge(municipio.get("nome", ""))

            if nome == cidade_norm:
                codigo = int(municipio["id"])
                self._cache[chave] = codigo
                return codigo

        if cep:
            municipio = self.obter_municipio_por_cep(cep)

            if municipio and municipio.get("codigo_ibge"):
                cidade_cep = municipio.get("cidade")
                uf_cep = municipio.get("uf")
                codigo = int(municipio["codigo_ibge"])

                chave_cep = (
                    self._normalizar_cidade_ibge(cidade_cep),
                    uf_cep.strip().upper()
                )

                self._cache[chave_cep] = codigo
                return codigo

        raise IbgeLookupError(f"Não encontrei código IBGE para {cidade}/{uf}.")