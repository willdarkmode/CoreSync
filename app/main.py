import json

from app.config import get_settings, validar_config
from app.logger import setup_logger
from app.validators import (
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
from app.services.idempotency_service import IdempotencyService


def processar_pedido(numero_pedido: str, settings, logger) -> None:
    cnpj_service = CnpjService(timeout=settings.timeout_padrao)

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

    logger.info("Gerando novo token Sankhya...")
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

    idempotency_service = None
    if settings.idempotency_enabled:
        idempotency_service = IdempotencyService(
            credentials_path=settings.firebase_credentials_path,
            project_id=settings.firebase_project_id,
            collection=settings.idempotency_collection,
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

    if idempotency_service:
        logger.info("Reservando pedido para idempotência...")
        idempotency_service.reservar(numero_pedido, payload)

    try:
        logger.info("Enviando pedido para Sankhya...")
        resposta = sankhya_client.incluir_pedido(payload)

        corrigir_cadastro_cliente_sankhya(
            sankhya_client=sankhya_client,
            payload=payload,
            resposta=resposta,
            logger=logger,
        )

    except Exception as exc:
        if idempotency_service:
            idempotency_service.marcar_falha(numero_pedido, exc)
        raise

    if idempotency_service:
        idempotency_service.marcar_sucesso(numero_pedido, resposta)

    print("\nResposta Sankhya:")
    print(json.dumps(resposta, indent=2, ensure_ascii=False))

    logger.info("Atualizando status do pedido na Wake para Separado...")

    resposta_status_wake = wake_client.atualizar_status_se_pago(
        numero_pedido=numero_pedido
    )

    if resposta_status_wake:
        print("\nResultado atualização status Wake:")
        print(json.dumps(resposta_status_wake, indent=2, ensure_ascii=False))

def corrigir_cadastro_cliente_sankhya(
    sankhya_client,
    payload: dict,
    resposta: dict,
    logger,
):
    try:
        codigo_pedido = (
            resposta.get("retorno", {}).get("codigoPedido")
            or resposta.get("codigoPedido")
            or resposta.get("codigo")
        )

        if not codigo_pedido:
            logger.warning(
                "Não foi possível atualizar cadastro do cliente: codigoPedido não encontrado. Resposta=%s",
                resposta,
            )
            return

        logger.info("Buscando CODPARC pelo pedido Sankhya %s...", codigo_pedido)

        codparc = sankhya_client.buscar_codparc_por_pedido(codigo_pedido)

        if not codparc:
            logger.warning(
                "Não foi possível atualizar cadastro do cliente: CODPARC não encontrado para o pedido %s.",
                codigo_pedido,
            )
            return

        logger.info(
            "Atualizando empresas do parceiro %s: CODEMP 1/2 com CODTAB 0",
            codparc,
        )

        retorno_empresas = sankhya_client.vincular_icms_por_empresa(codparc=codparc)

        logger.info(
            "Empresas do parceiro atualizadas com sucesso. Retorno=%s",
            retorno_empresas,
        )

        cliente_payload = payload.get("cliente", {})

        tipo = cliente_payload.get("tipo")
        ie = (cliente_payload.get("ieRg") or "").strip()
        classificms = cliente_payload.get("CLASSIFICMS")

        deve_corrigir_classificms = (
            tipo == "PJ"
            and ie
            and ie.upper() != "ISENTO"
            and classificms == "R"
        )

        if not deve_corrigir_classificms:
            return

        logger.info(
            "Atualizando CLASSIFICMS do parceiro %s para %s",
            codparc,
            classificms,
        )

        retorno_classificms = sankhya_client.atualizar_classificms_cliente(
            codparc=codparc,
            classificms=classificms,
        )

        logger.info(
            "CLASSIFICMS atualizado com sucesso. Retorno=%s",
            retorno_classificms,
        )

    except Exception as exc:
        logger.exception(
            "Erro ao atualizar cadastro do parceiro após inclusão do pedido: %s",
            exc,
        )

def main():
    settings = get_settings()
    validar_config(settings)
    logger = setup_logger(settings.log_level)

    while True:
        numero_pedido = input(
            "\nDigite o número do pedido Wake ou pressione ENTER para sair: "
        ).strip()

        if not numero_pedido:
            print("Encerrando sistema.")
            break

        try:
            processar_pedido(numero_pedido, settings, logger)
            print("\nPedido finalizado. Você pode digitar outro pedido.")

        except IntegracaoError as exc:
            logger.error("Erro de integração: %s", exc)
            print(f"\nErro de integração: {exc}")
            print("Você pode tentar outro pedido.")

        except Exception as exc:
            logger.exception("Erro inesperado")
            print(f"\nErro inesperado: {exc}")
            print("Você pode tentar outro pedido.")


if __name__ == "__main__":
    main()