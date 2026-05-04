import re
import requests
import unicodedata
from app.exceptions import IbgeLookupError
from app.utils import normalizar_texto


class IbgeService:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._cache: dict[tuple[str, str], int] = {}

    def remover_acentos(self, texto: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", texto)
            if unicodedata.category(c) != "Mn"
        )    

    def _normalizar_cidade_ibge(self, cidade: str) -> str:
        cidade = normalizar_texto(cidade)
        cidade = self.remover_acentos(cidade)
        cidade = re.sub(r"[^a-z0-9]+", " ", cidade)
        cidade = re.sub(r"\s+", " ", cidade).strip()
        return cidade

    def obter_codigo_ibge(self, cidade: str, uf: str) -> int:
        if not cidade or not uf:
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

        raise IbgeLookupError(f"Não encontrei código IBGE para {cidade}/{uf}.")