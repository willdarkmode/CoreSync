import math
import time
from datetime import datetime, timedelta

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
        return dt.replace(tzinfo=None)
    return dt


def pedido_atingiu_corte(pedido: dict, inicio_automatico: datetime) -> bool:
    candidatos = [
        pedido.get("dataPagamento"),
        pedido.get("dataUltimaAtualizacao"),
        pedido.get("data"),
    ]

    for valor in candidatos:
        if not valor:
            continue

        try:
            dt = normalizar_datetime_local(parse_datetime_iso_flex(valor))
            return dt >= inicio_automatico
        except (TypeError, ValueError):
            continue

    return False


def descobrir_pedidos_pago(
    wake_client: WakeClient,
    settings,
    inicio_automatico: datetime,
    logger,
) -> list[str]:
    agora = datetime.now()

    # A API filtra por data do pedido por padrão. O lookback permite capturar
    # pedidos criados antes da ativação que tenham sido pagos depois dela.
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

            pedidos_encontrados.append(str(pedido_id))

        if not pedidos:
            break

        paginas_totais = max(
            math.ceil((total or 0) / settings.automatic_page_size),
            1,
        )

        if pagina >= paginas_totais:
            break

        pagina += 1

    # Preserva a ordem do retorno e elimina eventuais duplicidades.
    return list(dict.fromkeys(pedidos_encontrados))


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

    logger.info(
        "%s pedido(s) elegível(is) encontrado(s): %s",
        len(pedidos),
        ", ".join(pedidos),
    )

    # O processamento é intencionalmente sequencial nesta primeira versão.
    for numero_pedido in reversed(pedidos):
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
    validar_config(settings)
    logger = setup_logger(settings.log_level)

    if not settings.permitir_envio:
        raise ValueError(
            "O modo automático só deve ser executado com PERMITIR_ENVIO=true."
        )

    if not settings.idempotency_enabled:
        raise ValueError(
            "O modo automático exige IDEMPOTENCY_ENABLED=true para impedir duplicidades."
        )

    inicio_automatico = parse_automatic_start_at(settings.automatic_start_at)

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
