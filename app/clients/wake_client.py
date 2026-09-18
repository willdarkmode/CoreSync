import time
import requests
from app.exceptions import WakeAPIError


class WakeClient:
    STATUS_PAGO = 1
    STATUS_SEPARADO = 16

    def __init__(self, base_url: str, auth: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "accept": "application/json",
            "Authorization": self.auth,
        }

    def _get_com_rate_limit(self, url: str, params: dict | None = None):
        while True:
            try:
                resp = requests.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=self.timeout,
                )

                if resp.status_code == 429:
                    try:
                        retry_after = int(resp.headers.get("retry-after", "1"))
                    except (TypeError, ValueError):
                        retry_after = 1

                    time.sleep(max(retry_after, 1))
                    continue

                resp.raise_for_status()
                return resp

            except requests.RequestException as exc:
                raise WakeAPIError(f"Falha ao consultar a Wake: {exc}") from exc

    def buscar_pedido(self, numero_pedido: str) -> dict:
        url = f"{self.base_url}/pedidos/{numero_pedido}"

        try:
            resp = self._get_com_rate_limit(url)
            return resp.json()
        except ValueError as exc:
            raise WakeAPIError(
                f"Resposta inválida ao buscar pedido {numero_pedido} na Wake"
            ) from exc

    def listar_pedidos_por_situacao(
        self,
        status_id: int,
        data_inicial: str,
        data_final: str,
        pagina: int = 1,
        quantidade_registros: int = 50,
    ) -> tuple[list[dict], int]:
        url = f"{self.base_url}/pedidos/situacaoPedido/{status_id}"

        params = {
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "pagina": pagina,
            "quantidadeRegistros": min(max(quantidade_registros, 1), 50),
            "apenasAssinaturas": "false",
        }

        resp = self._get_com_rate_limit(url, params=params)

        try:
            pedidos = resp.json()
        except ValueError as exc:
            raise WakeAPIError(
                "Resposta inválida ao consultar pedidos por situação na Wake"
            ) from exc

        if not isinstance(pedidos, list):
            raise WakeAPIError(
                f"Resposta inesperada ao consultar pedidos por situação: {pedidos}"
            )

        try:
            total = int(resp.headers.get("x-total-count") or len(pedidos))
        except (TypeError, ValueError):
            total = len(pedidos)

        return pedidos, total

    def obter_status_pedido(self, numero_pedido: str) -> int:
        url = f"{self.base_url}/pedidos/{numero_pedido}/status"

        try:
            resp = self._get_com_rate_limit(url)
            data = resp.json()

            if isinstance(data, dict) and data.get("situacaoPedidoId") is not None:
                return int(data["situacaoPedidoId"])

            raise WakeAPIError(
                f"Resposta inesperada ao obter status do pedido na Wake: {data}"
            )

        except ValueError as exc:
            raise WakeAPIError(
                f"Resposta inválida ao obter status do pedido {numero_pedido}"
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

    def atualizar_status_se_pago(
        self,
        numero_pedido: str,
        status_pago: int | None = None,
        status_separado: int | None = None,
    ) -> dict:
        status_pago = self.STATUS_PAGO if status_pago is None else status_pago
        status_separado = (
            self.STATUS_SEPARADO if status_separado is None else status_separado
        )

        status_atual = self.obter_status_pedido(numero_pedido)

        if status_atual != status_pago:
            return {
                "mensagem": (
                    f"Status atual ({status_atual}) não é o status Pago "
                    f"configurado ({status_pago}). Nenhuma ação realizada."
                )
            }

        return self.atualizar_status_pedido(
            numero_pedido=numero_pedido,
            status_id=status_separado,
        )