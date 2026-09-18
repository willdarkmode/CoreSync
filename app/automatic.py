import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.clients.wake_client import WakeClient
from app.config import get_settings, validar_config
from app.exceptions import IntegracaoError
from app.logger import setup_logger
from app.main import processar_pedido
from app.services.idempotency_service import (
    PedidoEmProcessamentoError,
    PedidoJaProcessadoError,
)
from app.utils import parse_datetime_iso_flex


FORMATO_DATA_WAKE = "%Y-%m-%d %H:%M:%S"
LOOKBACK_PEDIDOS_DIAS = 90
FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")


def parse_automatic_start_at(value: str) -> datetime:
    value = (value or "").strip()

    if not value:
        raise ValueError(
            "AUTOMATIC_START_AT não configurado. "
            "Defina a data/hora a partir da qual pedidos pagos podem ser integrados, "
            "por exemplo: 2026-09-17 14:00:00"
        )

    try:
        return datetime.strptime(value, FORMATO_DATA_WAKE)
    except ValueError as exc:
        raise ValueError(
            "AUTOMATIC_START_AT deve usar o formato YYYY-MM-DD HH:MM:SS"
        ) from exc


def normalizar_datetime_local(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(FUSO_LOCAL).replace(tzinfo=None)
    return dt


def parse_datetime_local(value: str) -> datetime:
    return normalizar_datetime_local(parse_datetime_iso_flex(value))


def obter_datetime_referencia(pedido: dict) -> datetime | None:
    candidatos = [
        pedido.get("dataPagamento"),
        pedido.get("dataUltimaAtualizacao"),
        pedido.get("data"),
    ]

    for valor in candidatos:
        if not valor:
            continue

        try:
            return parse_datetime_local(valor)
        except (TypeError, ValueError):
            continue

    return None


def pedido_atingiu_corte(pedido: dict, inicio_automatico: datetime) -> bool:
    dt_referencia = obter_datetime_referencia(pedido)
    return bool(dt_referencia and dt_referencia >= inicio_automatico)


def chave_ordenacao_pedido(pedido: dict) -> tuple[datetime, int]:
    dt_referencia = obter_datetime_referencia(pedido) or datetime.max

    try:
        pedido_id = int(pedido.get("pedidoId") or 0)
    except (TypeError, ValueError):
        pedido_id = 0

    return dt_referencia, pedido_id


def formatar_datetime_log(value: str | None) -> str:
    if not value:
        return "N/D"

    try:
        return parse_datetime_local(value).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def formatar_valor_brl(valor) -> str:
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError):
        numero = 0.0

    texto = f"{numero:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def obter_pedido_marketplace(pedido: dict) -> str:
    codigo = (
        pedido.get("marketPlacePedidoSiteId")
        or pedido.get("marketPlacePedidoId")
    )

    if codigo:
        return str(codigo)

    omnichannel = pedido.get("omnichannel") or {}
    return str(
        omnichannel.get("pedidoIdPrivado")
        or omnichannel.get("pedidoIdPublico")
        or "N/D"
    )


def obter_data_agendada_envio(pedido: dict) -> str:
    frete = pedido.get("frete") or {}
    informacoes = frete.get("informacoesAdicionais") or []

    for item in informacoes:
        chave = str(item.get("chave") or "").strip().lower()
        if chave != "data agendada de envio/coleta":
            continue

        valor = str(item.get("valor") or "").strip()
        if not valor:
            return "N/D"

        try:
            return parse_datetime_local(valor).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return valor

    return "N/D"


def descobrir_pedidos_pago(
    wake_client: WakeClient,
    settings,
    inicio_automatico: datetime,
    logger,
) -> list[dict]:
    agora = datetime.now()

    data_inicial_consulta = inicio_automatico - timedelta(days=LOOKBACK_PEDIDOS_DIAS)

    pagina = 1
    pedidos_encontrados = []
    total = None

    while True:
        pedidos, total_resposta = wake_client.listar_pedidos_por_situacao(
            status_id=settings.wake_status_pago,
            data_inicial=data_inicial_consulta.strftime(FORMATO_DATA_WAKE),
            data_final=agora.strftime(FORMATO_DATA_WAKE),
            pagina=pagina,
            quantidade_registros=settings.automatic_page_size,
        )

        if total is None:
            total = total_resposta
            logger.info(
                "Wake retornou %s pedido(s) no status Pago dentro da janela de consulta.",
                total,
            )

        for pedido in pedidos:
            pedido_id = pedido.get("pedidoId")

            if not pedido_id:
                continue

            if int(pedido.get("situacaoPedidoId") or 0) != settings.wake_status_pago:
                continue

            if not pedido_atingiu_corte(pedido, inicio_automatico):
                continue

            pedidos_encontrados.append(pedido)

        if not pedidos:
            break

        paginas_totais = max(
            math.ceil((total or 0) / settings.automatic_page_size),
            1,
        )

        if pagina >= paginas_totais:
            break

        pagina += 1

    pedidos_unicos = {}
    for pedido in pedidos_encontrados:
        pedido_id = str(pedido.get("pedidoId"))
        if pedido_id not in pedidos_unicos:
            pedidos_unicos[pedido_id] = pedido

    return sorted(
        pedidos_unicos.values(),
        key=chave_ordenacao_pedido,
    )


def reparar_status_wake(
    wake_client: WakeClient,
    numero_pedido: str,
    settings,
    logger,
) -> None:
    logger.info(
        "Pedido %s já consta como SUCCESS no Firebase. "
        "Verificando somente a finalização do status na Wake.",
        numero_pedido,
    )

    resposta = wake_client.atualizar_status_se_pago(
        numero_pedido=numero_pedido,
        status_pago=settings.wake_status_pago,
        status_separado=settings.wake_status_separado,
    )

    logger.info(
        "Finalização Wake do pedido %s processada: %s",
        numero_pedido,
        resposta,
    )


def executar_ciclo(settings, logger, inicio_automatico: datetime) -> None:
    wake_client = WakeClient(
        base_url=settings.wake_base_url,
        auth=settings.wake_auth,
        timeout=settings.timeout_padrao,
    )

    pedidos = descobrir_pedidos_pago(
        wake_client=wake_client,
        settings=settings,
        inicio_automatico=inicio_automatico,
        logger=logger,
    )

    if not pedidos:
        logger.info("Nenhum pedido novo elegível para integração neste ciclo.")
        return

    numeros_pedidos = [str(pedido.get("pedidoId")) for pedido in pedidos]

    logger.info(
        "%s pedido(s) elegível(is) encontrado(s), ordenados por data de pagamento "
        "do mais antigo para o mais novo: %s",
        len(pedidos),
        ", ".join(numeros_pedidos),
    )

    if settings.automatic_dry_run:
        logger.warning(
            "AUTOMATIC_DRY_RUN=true: somente leitura. Nenhum pedido será enviado "
            "ao Sankhya e nenhum status será alterado na Wake."
        )

        for pedido in pedidos:
            numero_pedido = str(pedido.get("pedidoId"))
            canal = pedido.get("canalNome") or pedido.get("canalOrigem") or "N/D"
            status_id = pedido.get("situacaoPedidoId")
            data_pagamento = formatar_datetime_log(pedido.get("dataPagamento"))
            pedido_marketplace = obter_pedido_marketplace(pedido)
            valor_total = formatar_valor_brl(pedido.get("valorTotalPedido"))
            data_agendada = obter_data_agendada_envio(pedido)

            logger.info(
                "[DRY-RUN] Pedido Wake=%s | Status=Pago (%s) | Pagamento=%s | "
                "Canal=%s | Pedido marketplace=%s | Valor=%s | "
                "Envio/coleta=%s",
                numero_pedido,
                status_id,
                data_pagamento,
                canal,
                pedido_marketplace,
                valor_total,
                data_agendada,
            )
        return

    for pedido in pedidos:
        numero_pedido = str(pedido.get("pedidoId"))

        try:
            logger.info("Iniciando integração automática do pedido %s.", numero_pedido)
            processar_pedido(numero_pedido, settings, logger)
            logger.info("Pedido %s finalizado com sucesso.", numero_pedido)

        except PedidoJaProcessadoError:
            try:
                reparar_status_wake(
                    wake_client=wake_client,
                    numero_pedido=numero_pedido,
                    settings=settings,
                    logger=logger,
                )
            except Exception:
                logger.exception(
                    "Pedido %s já estava integrado, mas não foi possível "
                    "reparar o status na Wake.",
                    numero_pedido,
                )

        except PedidoEmProcessamentoError as exc:
            logger.warning(
                "Pedido %s já está em processamento. Ignorando neste ciclo: %s",
                numero_pedido,
                exc,
            )

        except IntegracaoError as exc:
            logger.error(
                "Falha de integração no pedido %s: %s",
                numero_pedido,
                exc,
            )

        except Exception:
            logger.exception(
                "Erro inesperado no processamento automático do pedido %s.",
                numero_pedido,
            )


def main() -> None:
    settings = get_settings()
    logger = setup_logger(settings.log_level)

    if not settings.wake_auth:
        raise ValueError("WAKE_AUTH não configurado")

    inicio_automatico = parse_automatic_start_at(settings.automatic_start_at)

    if settings.automatic_dry_run:
        logger.warning(
            "CoreSync automático em DRY-RUN. Será executado somente um ciclo de leitura."
        )
        executar_ciclo(settings, logger, inicio_automatico)
        logger.info("Dry-run finalizado. Nenhuma alteração foi realizada.")
        return

    validar_config(settings)

    if not settings.permitir_envio:
        raise ValueError(
            "O modo automático real só deve ser executado com PERMITIR_ENVIO=true."
        )

    if not settings.idempotency_enabled:
        raise ValueError(
            "O modo automático exige IDEMPOTENCY_ENABLED=true para impedir duplicidades."
        )

    logger.info(
        "CoreSync automático iniciado. Corte=%s | intervalo=%ss | status Pago=%s | "
        "status Separado=%s",
        inicio_automatico.strftime(FORMATO_DATA_WAKE),
        settings.automatic_poll_seconds,
        settings.wake_status_pago,
        settings.wake_status_separado,
    )

    while True:
        try:
            executar_ciclo(settings, logger, inicio_automatico)
        except KeyboardInterrupt:
            logger.info("CoreSync automático encerrado pelo operador.")
            break
        except Exception:
            logger.exception("Falha no ciclo automático. O próximo ciclo será mantido.")

        try:
            time.sleep(settings.automatic_poll_seconds)
        except KeyboardInterrupt:
            logger.info("CoreSync automático encerrado pelo operador.")
            break


if __name__ == "__main__":
    main()
