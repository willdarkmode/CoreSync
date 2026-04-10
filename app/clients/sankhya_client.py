import requests
from app.exceptions import SankhyaAuthError, SankhyaAPIError


class SankhyaClient:
    def __init__(
        self,
        base_url: str,
        x_token: str,
        client_id: str,
        client_secret: str,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.x_token = x_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

    def obter_bearer_token(self) -> str:
        url = f"{self.base_url}/authenticate"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Token": self.x_token,
        }
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            response = requests.post(url, headers=headers, data=data, timeout=self.timeout)
            response.raise_for_status()
            dados = response.json()

            bearer_token = (
                dados.get("access_token")
                or dados.get("bearerToken")
                or dados.get("token")
            )

            if not bearer_token:
                raise SankhyaAuthError(f"Token não encontrado na resposta: {dados}")

            return bearer_token

        except requests.RequestException as exc:
            raise SankhyaAuthError(f"Falha na autenticação Sankhya: {exc}") from exc

    def incluir_pedido(self, payload: dict) -> dict:
        bearer_token = self.obter_bearer_token()

        url = f"{self.base_url}/v1/vendas/pedidos"
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return {"raw_response": response.text, "status_code": response.status_code}

        except requests.HTTPError as exc:
            detalhe = ""
            if exc.response is not None:
                try:
                    detalhe = exc.response.text
                except Exception:
                    detalhe = "<sem corpo de resposta>"
            raise SankhyaAPIError(f"Erro HTTP ao incluir pedido: {detalhe}") from exc

        except requests.RequestException as exc:
            raise SankhyaAPIError(f"Falha ao enviar pedido para Sankhya: {exc}") from exc