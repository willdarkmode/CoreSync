import requests


class FiscalClient:
    def __init__(self, base_url: str, auth_token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token = auth_token
        self.session = requests.Session()
        self.session.headers.update({
            "accept": "application/json",
            "content-type": "application/json",
        })

    def calcular_impostos(self, payload: dict) -> dict:
        url = f"{self.base_url}/v1/fiscal/impostos/calculo"

        headers = {
            "Authorization": f"Bearer {self.auth_token}"
        }

        response = self.session.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout
        )

        try:
            response.raise_for_status()

        except requests.HTTPError as exc:
            raise requests.HTTPError(
                (
                    f"Erro ao calcular impostos no Sankhya. "
                    f"status={response.status_code} | "
                    f"resposta={response.text} | "
                    f"payload={payload}"
                ),
                response=response,
            ) from exc

        return response.json()