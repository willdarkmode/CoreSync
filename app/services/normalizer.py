from decimal import Decimal

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
                logger.info(
                    "IE enriquecida via CNPJ.ws para CNPJ %s",
                    cnpj_cpf
                )
    except Exception as exc:
        if logger:
            logger.warning(
                "Falha ao enriquecer IE via CNPJ.ws para CNPJ %s: %s",
                cnpj_cpf,
                exc
            )

    return cliente


def calcular_valor_unitario_final(item: dict) -> float:
    quantidade = safe_float(item.get("quantidade", 1), 1.0)
    if quantidade <= 0:
        quantidade = 1.0

    valor_base = safe_float(item.get("valorItem", 0), 0.0)

    total_ajustes = 0.0
    for ajuste in item.get("ajustes", []):
        total_ajustes += safe_float(ajuste.get("valor", 0), 0.0)

    valor_total_item = valor_base + total_ajustes
    valor_unitario = valor_total_item / quantidade
    return round(valor_unitario, 2)


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

        info_debug = pagamento.get("informacoesAdicionais", [])
        print("\n[DEBUG PAGAMENTO]")
        print(f"numero_parcelas={numero_parcelas}")
        print(f"tipo_pagamento_sankhya={tipo_pagamento}")
        print(f"eh_cartao={pagamento_mapper.eh_cartao(pagamento)}")
        print(f"eh_pix={pagamento_mapper.eh_pix(pagamento)}")
        print(f"eh_boleto={pagamento_mapper.eh_boleto(pagamento)}")
        print(f"informacoesAdicionais={info_debug}")

        valor_total_pagamento = to_decimal(pagamento.get("valorTotal", 0))
        valor_parcela_wake = to_decimal(pagamento.get("valorParcela", 0))

        # Preferência:
        # 1) usar valorTotal do bloco e redistribuir com precisão em 2 casas
        # 2) se não houver, cair para valor_total do pedido
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

        # Distribuição correta em 2 casas:
        # Ex.: 412.86 / 8 => 51.61 x 7 + 51.59
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
    zerar_ipi_itens: bool = False,
) -> list[dict]:
    impostos = []

    if zerar_ipi_itens:
        impostos.append({
            "tipo": "ipi",
            "aliquota": 100,
            "valorImposto": 0,
            "valorBase": 0,
            "reducaoAliquota": 100
        })

    return impostos


def normalizar_pedido_wake(
    pedido_wake: dict,
    codigo_local_estoque: int,
    produto_mapper: ProdutoMapper,
    pagamento_mapper: PagamentoMapper,
    cnpj_service=None,
    logger=None,
    zerar_ipi_itens: bool = False,
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
            zerar_ipi_itens=zerar_ipi_itens,
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