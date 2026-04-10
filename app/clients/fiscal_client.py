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

        response.raise_for_status()
        return response.json()