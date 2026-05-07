import requests
import time
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

        self._access_token = None
        self._access_token_expires_at = None     

    def obter_bearer_token(self) -> str:
        return self.get_access_token()

    def get_access_token(self) -> str:
        
        if self._access_token and self._access_token_expires_at:
            if time.time() < self._access_token_expires_at:
                return self._access_token

        url = f"{self.base_url}/authenticate"

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Token": self.x_token,
        }

        try:
            response = requests.post(
                url,
                data=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise SankhyaAuthError(f"Falha ao autenticar no Sankhya: {exc}") from exc
        except ValueError as exc:
            raise SankhyaAuthError("Resposta de autenticação não é um JSON válido") from exc

        access_token = (
            data.get("access_token")
            or data.get("bearerToken")
            or data.get("token")
        )

        if not access_token:
            raise SankhyaAuthError(f"Resposta de autenticação sem access_token: {data}")

        try:
            expires_in = int(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600

        self._access_token = access_token
        self._access_token_expires_at = time.time() + expires_in - 60

        return self._access_token

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
        
    def buscar_codparc_por_pedido(self, codigo_pedido: str):
        bearer_token = self.obter_bearer_token()

        url = (
            f"{self.base_url}"
            "/gateway/v1/mge/service.sbr"
            "?serviceName=CRUDServiceProvider.loadRecords&outputType=json"
        )

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        }

        payload = {
            "serviceName": "CRUDServiceProvider.loadRecords",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "CabecalhoNota",
                    "includePresentationFields": "N",
                    "offsetPage": "0",
                    "criteria": {
                        "expression": {
                            "$": "NUNOTA = ?"
                        },
                        "parameter": [
                            {
                                "$": str(codigo_pedido),
                                "type": "I"
                            }
                        ]
                    },
                    "entity": {
                        "fieldset": {
                            "list": "NUNOTA,CODPARC"
                        }
                    }
                }
            }
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise SankhyaAPIError(
                f"Erro ao buscar CODPARC pelo pedido {codigo_pedido}: "
                f"status={response.status_code} body={response.text}"
            ) from exc

        dados = response.json()

        try:
            entity = dados["responseBody"]["entities"]["entity"]

            if isinstance(entity, dict):
                return int(entity["f1"]["$"])

            if isinstance(entity, list) and entity:
                return int(entity[0]["f1"]["$"])

        except Exception:
            raise SankhyaAPIError(
                f"Não foi possível extrair CODPARC da resposta do pedido {codigo_pedido}: {dados}"
            )

        return None   

    def atualizar_classificms_cliente(self, codparc: int, classificms: str) -> dict:
        bearer_token = self.obter_bearer_token()

        url = (
            f"{self.base_url}"
            "/gateway/v1/mge/service.sbr"
            "?serviceName=DatasetSP.save&outputType=json"
        )

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        }

        payload = {
            "serviceName": "DatasetSP.save",
            "requestBody": {
                "entityName": "Parceiro",
                "standAlone": False,
                "fields": [
                    "CODPARC",
                    "CLASSIFICMS",
                ],
                "records": [
                    {
                        "pk": {
                            "CODPARC": str(codparc)
                        },
                        "values": {
                            "1": classificms
                        }
                    }
                ]
            }
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise SankhyaAPIError(
                f"Erro ao atualizar CLASSIFICMS do parceiro {codparc}: "
                f"status={response.status_code} body={response.text}"
            ) from exc

        try:
            return response.json()
        except Exception:
            return {
                "status_code": response.status_code,
                "response_text": response.text,
            }  
        
    def _validar_retorno_datasetsp_save(
        self,
        retorno: dict,
        contexto: str,
        aceitar_registro_ja_existente: bool = False,
    ) -> None:
        if str(retorno.get("status")) == "1":
            return

        status_message = retorno.get("statusMessage") or ""

        if (
            aceitar_registro_ja_existente
            and "restrição exclusiva" in status_message.lower()
        ):
            return

        raise SankhyaAPIError(
            f"DatasetSP.save não confirmou a gravação de {contexto}. "
            f"Retorno={retorno}"
        )       
        
    def vincular_icms_por_empresa(
        self,
        codparc: int,
        empresas: list[tuple[str, str, str]] | None = None,
    ) -> dict:
        if empresas is None:
            empresas = [
                ("1", "1", "0"),
                ("2", "1", "0"),
            ]

        bearer_token = self.obter_bearer_token()

        url = (
            f"{self.base_url}"
            "/gateway/v1/mge/service.sbr"
            "?serviceName=DatasetSP.save&outputType=json"
        )

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        }

        records = [
            {
                "values": {
                    "0": str(codparc),
                    "1": str(codemp),
                    "2": str(codtab),
                }
            }
            for codemp, _, codtab in empresas
        ]

        payload = {
            "serviceName": "DatasetSP.save",
            "requestBody": {
                "entityName": "ParceiroEmpresGrupoIcms",
                "standAlone": False,
                "fields": [
                    "CODPARC",
                    "CODEMP",
                    "CODTAB",
                ],
                "records": records,
            },
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise SankhyaAPIError(
                f"Erro HTTP ao vincular ICMS por empresa do parceiro {codparc}: "
                f"status={response.status_code} body={response.text}"
            ) from exc

        retorno = response.json()

        self._validar_retorno_datasetsp_save(
            retorno,
            contexto=f"vínculo ICMS por empresa do parceiro {codparc}",
            aceitar_registro_ja_existente=True,
        )

        return retorno