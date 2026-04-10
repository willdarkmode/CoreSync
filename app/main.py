import json

from app.config import get_settings
from app.logger import setup_logger
from app.validators import (
    validar_config,
    validar_pedido_wake_bruto,
    validar_pedido_normalizado,
)
from app.clients.wake_client import WakeClient
from app.clients.sankhya_client import SankhyaClient
from app.clients.fiscal_client import FiscalClient
from app.services.cnpj_service import CnpjService
from app.services.ibge_service import IbgeService
from app.services.payload_builder import PayloadBuilder
from app.services.ipi_service import IpiCompensationService
from app.services.normalizer import normalizar_pedido_wake
from app.mappers import ProdutoMapper, PagamentoMapper
from app.exceptions import IntegracaoError


def main():
    settings = get_settings()
    logger = setup_logger(settings.log_level)
    cnpj_service = CnpjService(timeout=settings.timeout_padrao)

    try:
        validar_config(settings)

        wake_client = WakeClient(
            base_url=settings.wake_base_url,
            auth=settings.wake_auth,
            timeout=settings.timeout_padrao,
        )

        sankhya_client = SankhyaClient(
            base_url=settings.sankhya_base_url,
            x_token=settings.sankhya_x_token,
            client_id=settings.sankhya_client_id,
            client_secret=settings.sankhya_client_secret,
            timeout=settings.timeout_padrao,
        )

        access_token = sankhya_client.get_access_token()

        fiscal_client = FiscalClient(
            base_url=settings.sankhya_base_url,
            auth_token=access_token,
            timeout=settings.timeout_padrao,
        )

        ipi_service = IpiCompensationService(
            fiscal_client=fiscal_client,
            nota_modelo=settings.nota_modelo,
            codigo_cliente_referencia=settings.codigo_cliente_fiscal_referencia,
            codigo_empresa=settings.codigo_empresa,
            unidade_padrao=settings.unidade_padrao,
            logger=logger,
        )

        ibge_service = IbgeService(timeout=settings.timeout_padrao)
        produto_mapper = ProdutoMapper()
        pagamento_mapper = PagamentoMapper(settings.tipo_pagamento_padrao)

        payload_builder = PayloadBuilder(
            ibge_service=ibge_service,
            produto_mapper=produto_mapper,
            pagamento_mapper=pagamento_mapper,
            codigo_local_estoque=settings.codigo_local_estoque,
            nota_modelo=settings.nota_modelo,
            codigo_vendedor=settings.codigo_vendedor,
            cnpj_service=cnpj_service,
            logger=logger,
            ipi_strategy=settings.ipi_strategy,
        )

        numero_pedido = input("Digite o número do pedido Wake: ").strip()
        logger.info("Buscando pedido na Wake: %s", numero_pedido)

        pedido_wake = wake_client.buscar_pedido(numero_pedido)
        validar_pedido_wake_bruto(pedido_wake)

        logger.info("Normalizando pedido Wake...")
        pedido_normalizado = normalizar_pedido_wake(
            pedido_wake=pedido_wake,
            codigo_local_estoque=settings.codigo_local_estoque,
            produto_mapper=produto_mapper,
            pagamento_mapper=pagamento_mapper,
            cnpj_service=cnpj_service,
            logger=logger,
            ipi_strategy=settings.ipi_strategy,
        )

        validar_pedido_normalizado(pedido_normalizado)

        logger.info("Calculando compensações de IPI...")
        compensacoes = ipi_service.calcular_compensacoes(
            itens=pedido_normalizado["itens"],
        )

        comp_map = {int(c["sequencia"]): c for c in compensacoes}

        for item in pedido_normalizado["itens"]:
            comp = comp_map.get(int(item["sequencia"]))

            if not comp:
                continue

            item["valorDesconto"] = comp["valorDesconto"]
            item["aliquotaIpiCompensada"] = comp["aliquotaIpi"]

        logger.info("Montando payload Sankhya...")
        payload, pedido_normalizado = payload_builder.montar_com_pedido_normalizado(
            pedido_normalizado
        )

        print("\nPayload final:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        confirmar = input("\nEnviar para o Sankhya? (s/n): ").strip().lower()
        if confirmar != "s":
            print("Envio cancelado pelo usuário.")
            return

        if not settings.permitir_envio:
            print("PERMITIR_ENVIO=false. Apenas simulação.")
            return

        logger.info("Enviando pedido para Sankhya...")
        resposta = sankhya_client.incluir_pedido(payload)

        print("\nResposta Sankhya:")
        print(json.dumps(resposta, indent=2, ensure_ascii=False))

    except IntegracaoError as exc:
        logger.error("Erro de integração: %s", exc)
        print(f"\nErro de integração: {exc}")

    except Exception as exc:
        logger.exception("Erro inesperado")
        print(f"\nErro inesperado: {exc}")


if __name__ == "__main__":
    main()