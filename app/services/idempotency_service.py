import hashlib
import json
import socket
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore

from app.exceptions import IntegracaoError


class PedidoJaProcessadoError(IntegracaoError):
    pass


class PedidoEmProcessamentoError(IntegracaoError):
    pass


class IdempotencyService:
    def __init__(self, credentials_path: str, project_id: str, collection: str):
        if not firebase_admin._apps:
            if credentials_path:
                cred = credentials.Certificate(credentials_path)
                firebase_admin.initialize_app(cred, {"projectId": project_id or None})
            else:
                firebase_admin.initialize_app()

        self.db = firestore.client()
        self.collection = collection
        self.machine_id = socket.gethostname()

    @staticmethod
    def payload_hash(payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def reservar(self, numero_pedido: str, payload: dict) -> None:
        doc_ref = self.db.collection(self.collection).document(str(numero_pedido))
        transaction = self.db.transaction()
        payload_hash = self.payload_hash(payload)
        now = datetime.now(timezone.utc)

        @firestore.transactional
        def _reservar(transaction):
            snapshot = doc_ref.get(transaction=transaction)

            if snapshot.exists:
                data = snapshot.to_dict() or {}
                status = data.get("status")

                if status == "SUCCESS":
                    raise PedidoJaProcessadoError(
                        f"Pedido {numero_pedido} já foi integrado com sucesso."
                    )

                if status == "PROCESSING":
                    raise PedidoEmProcessamentoError(
                        f"Pedido {numero_pedido} já está em processamento por outra máquina."
                    )

            transaction.set(doc_ref, {
                "numero_pedido_wake": str(numero_pedido),
                "status": "PROCESSING",
                "payload_hash": payload_hash,
                "machine_id": self.machine_id,
                "created_at": now,
                "updated_at": now,
                "erro": None,
            }, merge=True)

        _reservar(transaction)

    def marcar_sucesso(self, numero_pedido: str, resposta: dict) -> None:
        self.db.collection(self.collection).document(str(numero_pedido)).set({
            "status": "SUCCESS",
            "sankhya_response": resposta,
            "updated_at": datetime.now(timezone.utc),
            "machine_id": self.machine_id,
            "erro": None,
        }, merge=True)

    def marcar_falha(self, numero_pedido: str, erro: Exception) -> None:
        self.db.collection(self.collection).document(str(numero_pedido)).set({
            "status": "FAILED",
            "erro": str(erro),
            "updated_at": datetime.now(timezone.utc),
            "machine_id": self.machine_id,
        }, merge=True)