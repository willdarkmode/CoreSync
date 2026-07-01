import json
from copy import deepcopy
from datetime import datetime, timedelta
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

MENSAGEM_COLISAO_DATA_HORA_SANKHYA = (
    "Já existe um registro de venda com estes dados"
)

MAX_TENTATIVAS_AJUSTE_HORARIO = 5


def erro_colisao_data_hora_sankhya(exc: Exception) -> bool:
    return MENSAGEM_COLISAO_DATA_HORA_SANKHYA in str(exc)


def deslocar_data_hora_payload(payload: dict, segundos: int) -> dict:
    novo_payload = deepcopy(payload)

    data = novo_payload.get("data")
    hora = novo_payload.get("hora")

    if not data or not hora:
        return novo_payload

    dt = datetime.strptime(
        f"{data} {hora}",
        "%d/%m/%Y %H:%M:%S",
    )

    dt = dt + timedelta(seconds=segundos)

    novo_payload["data"] = dt.strftime("%d/%m/%Y")
    novo_payload["hora"] = dt.strftime("%H:%M:%S")

    observacao = novo_payload.get("AD_OBSFIN") or ""

    aviso = (
        f"Ajuste técnico CoreSync: horário deslocado em "
        f"+{segundos}s por colisão de venda no Sankhya."
    )

    if aviso not in observacao:
        novo_payload["AD_OBSFIN"] = (
            f"{observacao}\n\n{aviso}"
        ).strip()

    return novo_payload


def enviar_pedido_sankhya_com_retry_colisao(
    sankhya_client,
    payload: dict,
    numero_pedido: str,
    logger,
):
    try:
        return sankhya_client.incluir_pedido(payload), payload

    except Exception as exc:
        if not erro_colisao_data_hora_sankhya(exc):
            raise

        logger.warning(
            "Colisão de data/hora detectada no Sankhya "
            "para pedido Wake %s. Tentando horário ajustado.",
            numero_pedido,
        )

        ultimo_erro = exc

        for segundos in range(
            1,
            MAX_TENTATIVAS_AJUSTE_HORARIO + 1,
        ):
            payload_ajustado = deslocar_data_hora_payload(
                payload,
                segundos,
            )

            logger.warning(
                "Tentativa +%ss para pedido %s (%s %s)",
                segundos,
                numero_pedido,
                payload_ajustado.get("data"),
                payload_ajustado.get("hora"),
            )

            try:
                resposta = sankhya_client.incluir_pedido(
                    payload_ajustado
                )

                logger.warning(
                    "Pedido %s integrado com horário ajustado "
                    "em +%ss.",
                    numero_pedido,
                    segundos,
                )

                return resposta, payload_ajustado

            except Exception as retry_exc:
                ultimo_erro = retry_exc

                if not erro_colisao_data_hora_sankhya(retry_exc):
                    raise

        raise ultimo_erro

def imprimir_resumo_payload(payload: dict) -> None:
    cliente = payload.get("cliente", {})
    itens = payload.get("itens", [])
    financeiros = payload.get("financeiros", [])

    print("\nResumo do pedido:")
    print(f"Cliente: {cliente.get('nome') or cliente.get('razao')}")
    print(f"Documento: {cliente.get('cnpjCpf')}")
    print(f"Tipo cliente: {cliente.get('tipo')}")
    print(f"Valor total: R$ {payload.get('valorTotal')}")
    print(f"Itens: {len(itens)}")
    print(f"Parcelas: {len(financeiros)}")
    print(f"Vendedor: {payload.get('codigoVendedor')}")
    print(f"Previsão entrega: {payload.get('AD_PREVENT')}")

    if itens:
        print("\nProdutos:")

        for item in itens:
            codigo = item.get("codigoProduto")
            quantidade = item.get("quantidade")
            valor = item.get("valorUnitario")

            print(
                f" - SKU {codigo} | Qtd {quantidade} | Unit R$ {valor}"
            )

def montar_resumo_firebase(
    payload: dict,
    numero_pedido_wake: str,
    codigo_pedido_sankhya: str | None = None,
) -> dict:
    cliente = payload.get("cliente", {})
    itens = payload.get("itens", [])
    financeiros = payload.get("financeiros", [])

    return {
        "pedidoWake": str(numero_pedido_wake),
        "pedidoSankhya": str(codigo_pedido_sankhya) if codigo_pedido_sankhya else None,
        "cliente": cliente.get("nome") or cliente.get("razao"),
        "documento": cliente.get("cnpjCpf"),
        "tipoCliente": cliente.get("tipo"),
        "valorTotal": payload.get("valorTotal"),
        "quantidadeItens": len(itens),
        "quantidadeParcelas": len(financeiros),
        "vendedor": payload.get("codigoVendedor"),
        "previsaoEntrega": payload.get("AD_PREVENT"),
        "produtos": [
            {
                "sku": item.get("codigoProduto"),
                "quantidade": item.get("quantidade"),
                "valorUnitario": item.get("valorUnitario"),
            }
            for item in itens
        ],
        "classificms": cliente.get("CLASSIFICMS"),
    }

def processar_pedido(numero_pedido: str, settings, logger) -> None:
    cnpj_service = CnpjService(
        timeout=settings.timeout_padrao,
        cnpja_api_key=settings.cnpja_api_key,
    )

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

    cliente = pedido_normalizado["cliente"]
    tipo_cliente = cliente.get("tipo")
    ie_rg = (cliente.get("ieRg") or "").strip()

    cliente_eh_revenda = (
        tipo_cliente == "PJ"
        and ie_rg
        and ie_rg.upper() != "ISENTO"
    )

    codigo_cliente_fiscal_calculo = (
        settings.codigo_cliente_fiscal_referencia_revenda
        if cliente_eh_revenda
        else settings.codigo_cliente_fiscal_referencia
    )

    logger.info(
        "Cliente fiscal para cálculo de IPI: %s | tipo=%s | IE=%s | revenda=%s",
        codigo_cliente_fiscal_calculo,
        tipo_cliente,
        ie_rg,
        cliente_eh_revenda,
    )

    logger.info("Calculando compensações de IPI...")
    compensacoes = ipi_service.calcular_compensacoes(
        itens=pedido_normalizado["itens"],
        codigo_cliente=codigo_cliente_fiscal_calculo,
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

    imprimir_resumo_payload(payload)

    while True:
        confirmar = input(
            "\nEnviar para o Sankhya? "
            "(s = sim / n = não / d = detalhes): "
        ).strip().lower()

        if confirmar == "d":
            print("\nPayload completo:")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            continue

        if confirmar == "s":
            break

        print("Envio cancelado pelo usuário.")
        return

    if not settings.permitir_envio:
        print("PERMITIR_ENVIO=false. Apenas simulação.")
        return

    if idempotency_service:
        logger.info("Reservando pedido para idempotência...")
        idempotency_service.reservar(numero_pedido, payload)

    codparc_sankhya = None    

    try:
        logger.info("Enviando pedido para Sankhya...")

        resposta, payload = enviar_pedido_sankhya_com_retry_colisao(
            sankhya_client=sankhya_client,
            payload=payload,
            numero_pedido=numero_pedido,
            logger=logger,
        )

        codparc_sankhya = corrigir_cadastro_cliente_sankhya(
            sankhya_client=sankhya_client,
            payload=payload,
            resposta=resposta,
            logger=logger,
        )

        codigo_pedido_sankhya = (
            resposta.get("retorno", {}).get("codigoPedido")
            or resposta.get("codigoPedido")
            or resposta.get("codigo")
        )

        if codigo_pedido_sankhya:
            logger.info(
                "Confirmando pedido Sankhya automaticamente: %s",
                codigo_pedido_sankhya,
            )

            resposta_confirmacao = (
                sankhya_client.confirmar_pedido(
                    codigo_pedido_sankhya
                )
            )

            logger.info(
                "Pedido %s confirmado. Resposta=%s",
                codigo_pedido_sankhya,
                resposta_confirmacao,
            )

    except Exception as exc:
        if idempotency_service:
            idempotency_service.marcar_falha(numero_pedido, exc)
        raise

    codigo_pedido_sankhya = (
        resposta.get("retorno", {}).get("codigoPedido")
        or resposta.get("codigoPedido")
        or resposta.get("codigo")
    )

    resumo_firebase = montar_resumo_firebase(
        payload=payload,
        numero_pedido_wake=str(numero_pedido),
        codigo_pedido_sankhya=codigo_pedido_sankhya,
    )

    if idempotency_service:
        idempotency_service.marcar_sucesso(
            numero_pedido=numero_pedido,
            resposta=resposta,
            resumo=resumo_firebase,
        )

    print("\nPedido enviado para o Sankhya com sucesso.")

    if codigo_pedido_sankhya:
        print(f"Pedido Sankhya: {codigo_pedido_sankhya}")

    if codparc_sankhya:
        print(f"Parceiro Sankhya: {codparc_sankhya}")

    logger.info("Atualizando status do pedido na Wake para Separado...")

    resposta_status_wake = wake_client.atualizar_status_se_pago(
        numero_pedido=numero_pedido
    )

    print("\nWake:")

    if isinstance(resposta_status_wake, dict):
        mensagem_wake = resposta_status_wake.get(
            "mensagem",
            "Status Wake processado.",
        )
        print(mensagem_wake)

    elif resposta_status_wake is True:
        print("Status atualizado com sucesso.")

    elif resposta_status_wake is False:
        print("Status não foi atualizado.")

    else:
        print("Status Wake processado.")

def corrigir_cadastro_cliente_sankhya(
    sankhya_client,
    payload: dict,
    resposta: dict,
    logger,
) -> int | None:
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
            return None

        logger.info("Buscando CODPARC pelo pedido Sankhya %s...", codigo_pedido)

        codparc = sankhya_client.buscar_codparc_por_pedido(codigo_pedido)

        if int(codparc) == 1:
            cliente = payload.get("cliente", {})

            logger.error(
                "Pedido criado com CODPARC=1. Cliente=%s | Resposta Sankhya=%s",
                cliente,
                resposta,
            )

            raise IntegracaoError(
                "Pedido criado com CODPARC=1. "
                "O Sankhya usou o parceiro padrão em vez de cadastrar/vincular o cliente. "
                "Integração bloqueada para evitar confirmação indevida."
            )

        if not codparc:
            logger.warning(
                "Não foi possível atualizar cadastro do cliente: CODPARC não encontrado para o pedido %s.",
                codigo_pedido,
            )
            return None

        logger.info(
            "Atualizando empresas do parceiro %s: CODEMP 1/2 com CODTAB 0",
            codparc,
        )

        sankhya_client.vincular_icms_por_empresa(codparc=codparc)

        logger.info(
            "Empresas 1 e 2 vinculadas ao parceiro %s com CODTAB 0",
            codparc,
        )

        cliente_payload = payload.get("cliente", {})
        endereco_payload = cliente_payload.get("endereco", {})

        classificms_payload = cliente_payload.get("CLASSIFICMS")

        if classificms_payload:
            logger.info(
                "Atualizando CLASSIFICMS do parceiro %s para %s.",
                codparc,
                classificms_payload,
            )

            sankhya_client.atualizar_classificms_cliente(
                codparc=codparc,
                classificms=classificms_payload,
            )

            logger.info(
                "CLASSIFICMS do parceiro %s atualizado para %s com sucesso.",
                codparc,
                classificms_payload,
            )
        else:
            logger.warning(
                "CLASSIFICMS não encontrado no payload do cliente para o parceiro %s.",
                codparc,
            )

        logger.info("Atualizando endereço do parceiro %s.", codparc)

        sankhya_client.atualizar_endereco_cliente(
            codparc=codparc,
            endereco=endereco_payload,
        )

        logger.info("Endereço do parceiro %s atualizado com sucesso.", codparc)

        
        return codparc

    except IntegracaoError:
        raise

    except Exception as exc:
        logger.exception(
            "Erro ao atualizar cadastro do parceiro após inclusão do pedido: %s",
            exc,
        )
        return None

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