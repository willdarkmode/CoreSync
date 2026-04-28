import requests
from app.exceptions import WakeAPIError


class WakeClient:
    STATUS_PAGO = 1
    STATUS_SEPARADO = 16

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

    def obter_status_pedido(self, numero_pedido: str) -> int:
        url = f"{self.base_url}/pedidos/{numero_pedido}/status"

        headers = {
            "accept": "application/json",
            "Authorization": self.auth,
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()

            data = resp.json()

            if isinstance(data, dict) and data.get("situacaoPedidoId") is not None:
                return int(data["situacaoPedidoId"])

            raise WakeAPIError(
                f"Resposta inesperada ao obter status do pedido na Wake: {data}"
            )

        except requests.RequestException as exc:
            raise WakeAPIError(
                f"Falha ao obter status do pedido na Wake: {exc}"
            ) from exc

    def atualizar_status_pedido(self, numero_pedido: str, status_id: int) -> dict:
        url = f"{self.base_url}/pedidos/{numero_pedido}/status"

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": self.auth,
        }

        payload = {"id": status_id}

        try:
            resp = requests.put(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()

            try:
                return resp.json()
            except ValueError:
                return {
                    "raw_response": resp.text,
                    "status_code": resp.status_code,
                }

        except requests.RequestException as exc:
            raise WakeAPIError(
                f"Falha ao atualizar status do pedido na Wake: {exc}"
            ) from exc

    def atualizar_status_se_pago(self, numero_pedido: str) -> dict:
        status_atual = self.obter_status_pedido(numero_pedido)

        if status_atual != self.STATUS_PAGO:
            return {
                "mensagem": f"Status atual ({status_atual}) não é Pago. Nenhuma ação realizada."
            }

        return self.atualizar_status_pedido(
            numero_pedido=numero_pedido,
            status_id=self.STATUS_SEPARADO,
        )