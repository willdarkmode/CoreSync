from decimal import Decimal
import re
from datetime import timedelta

from app.utils import (
    somente_digitos,
    quebrar_telefone,
    formatar_data_hora_br,
    parse_datetime_iso_flex,
    adicionar_meses,
    safe_float,
    to_decimal,
    money_2,
)
from app.mappers import ProdutoMapper, PagamentoMapper

TRANSPORTADORAS_CONFIG = [
    {
        "codigo": 3268,
        "match_exato": ["jadlog package", "jadlog"],
    },
    {
        "codigo": 2994,
        "match_exato": ["pac", "sedex"],
        "match_contem": ["correios"],
    },
    {
        "codigo": 3090,
        "match_exato": ["mercado livre"],
    },
]

CONDICOES_PAGAMENTO_MAP = {
    1: 11,    # A VISTA / Pix
    2: 89,    # 2X CARTAO
    3: 107,   # 3X CARTAO
    4: 106,   # 4X CARTAO
    5: 105,   # 5X CARTAO
    6: 104,   # 6X CARTAO
    7: 103,   # 7X CARTAO
    8: 102,   # 8X CARTAO
    9: 101,   # 9X CARTAO
    10: 100,  # 10X CARTAO
    12: 165,  # 12X CARTAO
}

def obter_endereco_entrega(pedido_wake: dict) -> dict:
    enderecos = pedido_wake.get("pedidoEndereco", [])
    for endereco in enderecos:
        if endereco.get("tipo") == "Entrega":
            return endereco
    return enderecos[0] if enderecos else {}


def obter_telefone_usuario(usuario: dict) -> str:
    return (
        usuario.get("telefoneCelular")
        or usuario.get("telefoneResidencial")
        or usuario.get("telefoneComercial")
        or ""
    )


def obter_tipo_cliente(usuario: dict) -> str:
    tipo = (usuario.get("tipoPessoa") or "").strip().lower()
    return "PF" if tipo == "fisica" else "PJ"


def enriquecer_ie_cliente(cliente: dict, cnpj_service=None, logger=None) -> dict:
    tipo = cliente.get("tipo")
    cnpj_cpf = cliente.get("cnpjCpf", "")
    ie_atual = (cliente.get("ieRg") or "").strip()

    if tipo != "PJ":
        return cliente
    if ie_atual:
        return cliente
    if not cnpj_service:
        return cliente

    try:
        dados = cnpj_service.buscar_dados_cnpj(cnpj_cpf)
        if dados and dados.get("inscricao_estadual"):
            cliente["ieRg"] = dados["inscricao_estadual"]
            if logger:
                logger.info("IE enriquecida via CNPJ.ws para CNPJ %s", cnpj_cpf)
    except Exception as exc:
        if logger:
            logger.warning(
                "Falha ao enriquecer IE via CNPJ.ws para CNPJ %s: %s",
                cnpj_cpf,
                exc,
            )

    return cliente

def obter_valor_grupo_info_cadastral(usuario: dict, chave: str) -> str:
    grupos = usuario.get("grupoInformacaoCadastral") or []
    chave_normalizada = (chave or "").strip().lower()

    for item in grupos:
        chave_item = str(item.get("chave") or "").strip().lower()
        if chave_item == chave_normalizada:
            return str(item.get("valor") or "").strip()

    return ""

def obter_numero_parcelas_pedido(pedido_wake: dict) -> int:
    pagamentos = pedido_wake.get("pagamento") or []

    if not pagamentos:
        return 1

    maior_numero_parcelas = 1

    for pagamento in pagamentos:
        parcelas = int(safe_float(pagamento.get("numeroParcelas", 1), 1))
        maior_numero_parcelas = max(maior_numero_parcelas, parcelas)

    return maior_numero_parcelas


def obter_codigo_condicao_pagamento(pedido_wake: dict, logger=None) -> int:
    parcelas = obter_numero_parcelas_pedido(pedido_wake)

    codigo = CONDICOES_PAGAMENTO_MAP.get(parcelas)

    if codigo:
        if logger:
            logger.info(
                "Condição de pagamento identificada: %sx -> CODTIPVENDA=%s",
                parcelas,
                codigo,
            )
        return codigo

    if logger:
        logger.warning(
            "Condição de pagamento não mapeada para %sx. Usando A VISTA CODTIPVENDA=11",
            parcelas,
        )

    return 11

def extrair_dias_prazo_envio(pedido_wake: dict, logger=None) -> int | None:
    frete = pedido_wake.get("frete") or {}

    prazo_dias = frete.get("prazoEnvio")
    if prazo_dias is not None:
        try:
            dias = int(prazo_dias)
            if logger:
                logger.info(
                    "Prazo de envio identificado na Wake via prazoEnvio=%s dia(s)",
                    dias,
                )
            return dias
        except (TypeError, ValueError):
            if logger:
                logger.warning(
                    "Valor inválido em frete.prazoEnvio: %s",
                    prazo_dias,
                )

    prazo_texto = str(frete.get("prazoEnvioTexto") or "").strip().lower()
    if prazo_texto:
        import re

        match = re.search(r"(\d+)", prazo_texto)
        if match:
            dias = int(match.group(1))
            if logger:
                logger.info(
                    "Prazo de envio identificado na Wake via prazoEnvioTexto='%s' -> %s dia(s)",
                    prazo_texto,
                    dias,
                )
            return dias

        if logger:
            logger.warning(
                "Não foi possível extrair dias de frete.prazoEnvioTexto='%s'",
                prazo_texto,
            )

    if logger:
        logger.info("Prazo de envio não informado na Wake.")

    return None

def calcular_previsao_entrega(pedido_wake: dict, logger=None) -> str:
    data_pedido_raw = (
        pedido_wake.get("data")
        or pedido_wake.get("dataPedido")
        or pedido_wake.get("dataCriacao")
        or pedido_wake.get("criadoEm")
        or ""
    )

    dt_pedido = parse_datetime_iso_flex(data_pedido_raw)
    if not dt_pedido:
        if logger:
            logger.warning(
                "Não foi possível calcular AD_PREVENT: data do pedido inválida '%s'",
                data_pedido_raw,
            )
        return ""

    dias_prazo = extrair_dias_prazo_envio(pedido_wake, logger=logger)
    if dias_prazo is None:
        if logger:
            logger.info(
                "Prazo de envio não informado. AD_PREVENT não será preenchido."
            )
        return ""

    dt_prevista = dt_pedido + timedelta(days=dias_prazo)
    data_prevista = dt_prevista.strftime("%d/%m/%Y")

    if logger:
        logger.info(
            "Previsão de entrega calculada: data_base='%s' + %s dia(s) -> AD_PREVENT=%s",
            data_pedido_raw,
            dias_prazo,
            data_prevista,
        )

    return data_prevista

def normalizar_texto(valor: str) -> str:
    return str(valor or "").strip().lower()


def obter_codigo_transportadora(pedido_wake: dict, logger=None) -> int | None:
    frete = pedido_wake.get("frete") or {}

    candidatos = [
        frete.get("freteContrato"),
        frete.get("referenciaConector"),
        frete.get("grupoFreteNome"),
        pedido_wake.get("canalNome"),
        pedido_wake.get("canalOrigem"),
    ]

    candidatos = [
        normalizar_texto(c)
        for c in candidatos
        if c
    ]

    # 🔥 1. match exato (prioridade máxima)
    for nome in candidatos:
        for regra in TRANSPORTADORAS_CONFIG:
            for termo in regra.get("match_exato", []):
                if nome == termo:
                    return regra["codigo"]

    # 🔥 2. match parcial (fallback)
    for nome in candidatos:
        for regra in TRANSPORTADORAS_CONFIG:
            for termo in regra.get("match_contem", []):
                if termo in nome:
                    return regra["codigo"]

    if logger:
        logger.warning(f"Transportadora não identificada: {candidatos}")

    return None

def obter_sigla_canal(pedido_wake: dict) -> str:
    canal = (
        pedido_wake.get("canalNome")
        or pedido_wake.get("canalOrigem")
        or ""
    ).strip().lower()

    mapa = {
        "mercado livre": "ML",
        "magalu": "MAG",
        "amazon": "AMZ",
        "shopee": "SHP",
        "loja": "SITE",
    }

    return mapa.get(canal, canal.upper() if canal else "SITE")


def obter_codigo_venda(pedido_wake: dict) -> str:
    # prioridade: marketplace
    if pedido_wake.get("marketPlacePedidoId"):
        return str(pedido_wake["marketPlacePedidoId"])

    omnichannel = pedido_wake.get("omnichannel") or {}

    if omnichannel.get("pedidoIdPrivado"):
        return str(omnichannel["pedidoIdPrivado"])

    if omnichannel.get("pedidoIdPublico"):
        return str(omnichannel["pedidoIdPublico"])

    # fallback: pedido interno
    return str(pedido_wake.get("pedidoId") or "")


def formatar_data_obsfin(data_iso: str) -> str:
    dt = parse_datetime_iso_flex(data_iso)

    meses = {
        1: "jan", 2: "fev", 3: "mar", 4: "abr",
        5: "mai", 6: "jun", 7: "jul", 8: "ago",
        9: "set", 10: "out", 11: "nov", 12: "dez",
    }

    return f"{dt.day} {meses[dt.month]} {dt.strftime('%H:%M')} hs"


def montar_observacao_financeira(pedido_wake: dict) -> str:
    sigla = obter_sigla_canal(pedido_wake)
    codigo = obter_codigo_venda(pedido_wake)
    data = formatar_data_obsfin(pedido_wake.get("data"))

    return f"{sigla}\n\nVenda #{codigo} {data}"

def mapear_finalidade_compra_para_nufop(pedido_wake: dict, logger=None) -> int:
    usuario = pedido_wake.get("usuario") or {}
    finalidade = obter_valor_grupo_info_cadastral(usuario, "Finalidade de compra")
    finalidade_normalizada = finalidade.strip().lower()

    mapa = {
        "revenda": 5,
        "industrialização": 6,
        "industrializacao": 6,
        "uso e consumo": 4,
        "uso/consumo": 4,
    }

    nufop = mapa.get(finalidade_normalizada, 4)

    if logger:
        if finalidade:
            logger.info(
                "Finalidade de compra identificada na Wake: '%s' -> NUFOP=%s",
                finalidade,
                nufop,
            )
        else:
            logger.info(
                "Finalidade de compra não informada na Wake. Usando NUFOP padrão=%s",
                nufop,
            )

    return nufop

def obter_valor_frete(pedido_wake: dict) -> float:
    frete = pedido_wake.get("frete") or {}

    return round(
        safe_float(
            pedido_wake.get("valorFrete")
            or frete.get("valorFreteCliente")
            or frete.get("valorFreteEmpresa")
            or 0,
            0.0,
        ),
        2,
    ) 

def mapear_frete_para_cif_fob(pedido_wake: dict, logger=None) -> str:
    frete = pedido_wake.get("frete") or {}
    frete_contrato = str(frete.get("freteContrato") or "").strip()
    frete_contrato_normalizado = frete_contrato.lower()

    if "fob" in frete_contrato_normalizado:
        if logger:
            logger.info(
                "Tipo de frete identificado na Wake: '%s' -> CIF_FOB=F",
                frete_contrato,
            )
        return "F"

    if "cif" in frete_contrato_normalizado:
        if logger:
            logger.info(
                "Tipo de frete identificado na Wake: '%s' -> CIF_FOB=C",
                frete_contrato,
            )
        return "C"

    if logger:
        logger.info(
            "Tipo de frete não identificado na Wake. Usando CIF_FOB padrão=C"
        )

    return "C"

def calcular_valor_unitario_final(item: dict) -> float:
    valor_unitario = safe_float(
        item.get("valorItem")
        or item.get("precoPor")
        or item.get("precoVenda")
        or 0,
        0.0,
    )

    total_ajustes_unitario = 0.0
    quantidade = safe_float(item.get("quantidade", 1), 1.0)

    if quantidade <= 0:
        quantidade = 1.0

    for ajuste in item.get("ajustes", []):
        nome_ajuste = str(ajuste.get("nome") or "").strip().lower()

        if nome_ajuste == "frete":
            continue

        valor_ajuste = safe_float(ajuste.get("valor", 0), 0.0)

        # Se o ajuste vier como total do item, rateia por unidade
        total_ajustes_unitario += valor_ajuste / quantidade

    return round(valor_unitario + total_ajustes_unitario, 2)


def calcular_valor_total_item(item: dict) -> Decimal:
    quantidade = Decimal(str(safe_float(item.get("quantidade", 0), 0.0)))
    valor_unitario = Decimal(str(calcular_valor_unitario_final(item)))
    return money_2(quantidade * valor_unitario)


def extrair_aliquota_ipi_item(item_wake: dict) -> Decimal:
    """
    Tenta descobrir a alíquota de IPI no item da Wake.
    Ajuste esta função conforme o shape real do payload recebido.
    """
    candidatos_diretos = [
        item_wake.get("aliquotaIPI"),
        item_wake.get("aliquotaIpi"),
        item_wake.get("ipiAliquota"),
        item_wake.get("percentualIPI"),
        item_wake.get("percentualIpi"),
    ]

    for valor in candidatos_diretos:
        dec = to_decimal(valor)
        if dec > 0:
            return dec

    # procura em possíveis blocos de impostos
    blocos = []
    for chave in ("impostos", "tributos", "taxas"):
        valor = item_wake.get(chave)
        if isinstance(valor, list):
            blocos.extend(valor)

    for imposto in blocos:
        tipo = str(imposto.get("tipo") or imposto.get("nome") or "").strip().lower()
        if tipo == "ipi":
            for chave in ("aliquota", "percentual", "valorAliquota"):
                dec = to_decimal(imposto.get(chave))
                if dec > 0:
                    return dec

    return Decimal("0.00")


def calcular_compensacao_ipi_item(item_wake: dict) -> dict:
    """
    Calcula o desconto necessário para neutralizar o acréscimo do IPI no total.
    """
    base_item = calcular_valor_total_item(item_wake)
    aliquota_ipi = extrair_aliquota_ipi_item(item_wake)

    if base_item <= 0 or aliquota_ipi <= 0:
        return {
            "aliquotaIpi": 0.0,
            "valorBaseDesconto": float(base_item),
            "valorDesconto": 0.0,
            "percentualDesconto": 0.0,
        }

    valor_desconto = money_2(base_item * (aliquota_ipi / Decimal("100")))
    percentual_desconto = Decimal("0.00")

    if base_item > 0:
        percentual_desconto = money_2((valor_desconto / base_item) * Decimal("100"))

    return {
        "aliquotaIpi": float(aliquota_ipi),
        "valorBaseDesconto": float(base_item),
        "valorDesconto": float(valor_desconto),
        "percentualDesconto": float(percentual_desconto),
    }


def montar_financeiros(
    pedido_wake: dict,
    valor_total: float,
    pagamento_mapper: PagamentoMapper,
) -> list[dict]:
    pagamentos = pedido_wake.get("pagamento", [])
    data_base = pedido_wake.get("dataPagamento") or pedido_wake.get("data")
    data_base_dt = parse_datetime_iso_flex(data_base)
    valor_total_dec = money_2(to_decimal(valor_total))

    if not pagamentos:
        return [{
            "sequencia": 1,
            "tipoPagamento": pagamento_mapper.obter_tipo_pagamento(pedido_wake, None),
            "dataVencimento": data_base_dt.strftime("%d/%m/%Y"),
            "valorParcela": float(valor_total_dec),
        }]

    financeiros = []
    sequencia = 1
    total_gerado = Decimal("0.00")

    for pagamento in pagamentos:
        numero_parcelas = pagamento_mapper.obter_numero_parcelas(pagamento)
        tipo_pagamento = pagamento_mapper.obter_tipo_pagamento(pedido_wake, pagamento)
        offset_inicial = 1 if pagamento_mapper.primeira_parcela_no_proximo_mes(pagamento) else 0

        valor_total_pagamento = to_decimal(pagamento.get("valorTotal", 0))
        base_total = money_2(valor_total_pagamento) if valor_total_pagamento > 0 else valor_total_dec

        if numero_parcelas <= 1:
            valor = money_2(base_total)
            data_parcela = adicionar_meses(data_base_dt, offset_inicial)
            financeiros.append({
                "sequencia": sequencia,
                "tipoPagamento": tipo_pagamento,
                "dataVencimento": data_parcela.strftime("%d/%m/%Y"),
                "valorParcela": float(valor),
            })
            total_gerado += valor
            sequencia += 1
            continue

        valor_base = money_2(base_total / Decimal(numero_parcelas))
        acumulado_bloco = Decimal("0.00")

        for i in range(numero_parcelas):
            if i < numero_parcelas - 1:
                valor = valor_base
            else:
                valor = money_2(base_total - acumulado_bloco)

            data_parcela = adicionar_meses(data_base_dt, offset_inicial + i)
            financeiros.append({
                "sequencia": sequencia,
                "tipoPagamento": tipo_pagamento,
                "dataVencimento": data_parcela.strftime("%d/%m/%Y"),
                "valorParcela": float(valor),
            })
            acumulado_bloco += valor
            total_gerado += valor
            sequencia += 1

    total_gerado = money_2(total_gerado)

    if abs(total_gerado - valor_total_dec) > Decimal("0.01"):
        raise ValueError(
            f"Financeiros inconsistentes. valor_total={valor_total_dec} | total_financeiros={total_gerado}"
        )

    return financeiros


def montar_impostos_item(
    item_wake: dict,
    ipi_strategy: str = "discount_compensation",
) -> list[dict]:
    impostos = []

    if ipi_strategy == "force_zero_ipi":
        impostos.append({
            "tipo": "ipi",
            "aliquota": 100,
            "valorImposto": 0,
            "valorBase": 0,
            "reducaoAliquota": 100,
        })

    return impostos


def normalizar_pedido_wake(
    pedido_wake: dict,
    codigo_local_estoque: int,
    produto_mapper: ProdutoMapper,
    pagamento_mapper: PagamentoMapper,
    cnpj_service=None,
    logger=None,
    ipi_strategy: str = "discount_compensation",
) -> dict:
    usuario = pedido_wake.get("usuario", {})
    endereco = obter_endereco_entrega(pedido_wake)
    telefone = obter_telefone_usuario(usuario)
    ddd, numero_tel = quebrar_telefone(telefone)

    itens_norm = []

    for idx, item in enumerate(pedido_wake.get("itens", []), start=1):
        sku = item.get("sku", "")
        impostos_item = montar_impostos_item(
            item_wake=item,
            ipi_strategy=ipi_strategy,
        )

        item_norm = {
            "sequencia": idx,
            "codigoProduto": produto_mapper.sku_wake_para_codigo_sankhya(sku),
            "quantidade": safe_float(item.get("quantidade", 0), 0.0),
            "controle": "",
            "codigoLocalEstoque": codigo_local_estoque,
            "valorUnitario": calcular_valor_unitario_final(item),
            "skuOriginal": sku,
            "nomeProduto": item.get("nome", ""),
        }

        if ipi_strategy == "discount_compensation":
            compensacao = calcular_compensacao_ipi_item(item)
            item_norm["valorDesconto"] = compensacao["valorDesconto"]
            item_norm["percentualDesconto"] = compensacao["percentualDesconto"]
            item_norm["aliquotaIpiCompensada"] = compensacao["aliquotaIpi"]

            if logger and compensacao["valorDesconto"] > 0:
                logger.info(
                    "Compensação de IPI aplicada no item seq=%s sku=%s base=%.2f aliquota_ipi=%.4f desconto=%.2f perc_desc=%.4f",
                    idx,
                    sku,
                    compensacao["valorBaseDesconto"],
                    compensacao["aliquotaIpi"],
                    compensacao["valorDesconto"],
                    compensacao["percentualDesconto"],
                )

        if impostos_item:
            item_norm["impostos"] = impostos_item

        itens_norm.append(item_norm)

    data_fmt, hora_fmt = formatar_data_hora_br(pedido_wake["data"])
    valor_total = round(safe_float(pedido_wake.get("valorTotalPedido", 0), 0.0), 2)

    pedido = {
        "pedidoId": pedido_wake.get("pedidoId"),
        "identificador": pedido_wake.get("identificador"),
        "data": data_fmt,
        "hora": hora_fmt,
        "valorTotal": valor_total,
        "observacaoFinanceira": montar_observacao_financeira(pedido_wake),
        "nufop": mapear_finalidade_compra_para_nufop(pedido_wake, logger=logger),
        "cifFob": mapear_frete_para_cif_fob(pedido_wake, logger=logger),
        "previsaoEntrega": calcular_previsao_entrega(pedido_wake, logger=logger),
        "codigoTransportadora": obter_codigo_transportadora(pedido_wake),
        "valorFrete": obter_valor_frete(pedido_wake),
        "codigoCondicaoPagamento": obter_codigo_condicao_pagamento(
            pedido_wake,
            logger=logger,
        ),
        "cliente": {
            "atualizar": True,
            "tipo": obter_tipo_cliente(usuario),
            "endereco": {
                "logradouro": endereco.get("logradouro") or endereco.get("endereco") or "",
                "numero": endereco.get("numero", ""),
                "complemento": endereco.get("complemento", ""),
                "bairro": endereco.get("bairro", ""),
                "cidade": endereco.get("cidade", ""),
                "uf": endereco.get("estado", ""),
                "cep": somente_digitos(endereco.get("cep", "")),
            },
            "cnpjCpf": somente_digitos(usuario.get("cpf") or usuario.get("cnpj") or ""),
            "ieRg": usuario.get("rg") or usuario.get("inscricaoEstadual") or "",
            "nome": usuario.get("nome") or usuario.get("razaoSocial") or "",
            "email": usuario.get("email", ""),
            "telefoneNumero": numero_tel,
            "telefoneDdd": ddd,
        },
        "itens": itens_norm,
        "financeiros": montar_financeiros(pedido_wake, valor_total, pagamento_mapper),
    }

    pedido["cliente"] = enriquecer_ie_cliente(
        pedido["cliente"],
        cnpj_service=cnpj_service,
        logger=logger,
    )

    return pedido