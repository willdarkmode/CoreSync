import requests
from app.exceptions import WakeAPIError


class WakeClient:
    def __init__(self, base_url: str, auth: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    def buscar_pedido(self, numero_pedido: str) -> dict:
        url = f"{self.base_url}/pedidos/{numero_pedido}"
        headers = {
            "accept": "application/json",
            "Authorization": self.auth,
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise WakeAPIError(f"Falha ao buscar pedido na Wake: {exc}") from exc