from decimal import Decimal, ROUND_HALF_UP


def money_2(valor) -> Decimal:
    return Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class IpiCompensationService:
    def __init__(
        self,
        fiscal_client,
        nota_modelo: int,
        codigo_cliente_referencia: int,
        codigo_empresa: int | None = None,
        unidade_padrao: str = "UN",
        logger=None,
    ):
        self.fiscal_client = fiscal_client
        self.nota_modelo = nota_modelo
        self.codigo_cliente_referencia = codigo_cliente_referencia
        self.codigo_empresa = codigo_empresa
        self.unidade_padrao = unidade_padrao
        self.logger = logger

    def _extrair_aliquota_ipi(self, produto_calculado: dict) -> Decimal:
        for imposto in produto_calculado.get("impostos", []):
            if str(imposto.get("tipo", "")).strip().upper() == "IPI":
                return Decimal(str(imposto.get("aliquota", 0)))
        return Decimal("0.00")

    def _calcular_desconto_compensatorio(self, valor_total_item: Decimal, aliquota_ipi: Decimal) -> Decimal:
        """
        Calcula o desconto que faz o valor final do item permanecer igual ao valor original,
        considerando que o Sankhya recalcula o IPI sobre a base já descontada.

        Regra alvo:
            valor_total_item == (base_liquida + ipi(base_liquida))
            onde base_liquida = valor_total_item - desconto
        """
        if valor_total_item <= 0 or aliquota_ipi <= 0:
            return Decimal("0.00")

        taxa = aliquota_ipi / Decimal("100")

        # aproximação inicial
        desconto_aprox = (valor_total_item * taxa) / (Decimal("1.00") + taxa)
        desconto_aprox = money_2(desconto_aprox)

        # procura em centavos ao redor da aproximação para achar o ponto que fecha exato
        candidatos = []
        for delta_centavos in range(-5, 6):
            desconto = desconto_aprox + (Decimal(delta_centavos) / Decimal("100"))
            desconto = money_2(desconto)

            if desconto < 0 or desconto > valor_total_item:
                continue

            base_liquida = money_2(valor_total_item - desconto)
            valor_ipi = money_2(base_liquida * taxa)
            valor_final = money_2(base_liquida + valor_ipi)
            diferenca = abs(valor_final - valor_total_item)

            candidatos.append((diferenca, desconto, valor_final, valor_ipi))

        if not candidatos:
            return Decimal("0.00")

        # menor diferença; em empate, prefere o desconto mais próximo da aproximação
        candidatos.sort(key=lambda x: (x[0], abs(x[1] - desconto_aprox)))
        return candidatos[0][1]    

    def calcular_compensacoes(self, itens: list[dict]) -> list[dict]:
        produtos_payload = []

        for item in itens:
            produtos_payload.append({
                "codigoProduto": item["codigoProduto"],
                "unidade": "PC",
                "quantidade": item["quantidade"],
                "valorUnitario": item["valorUnitario"],
                "valorDesconto": 0,
            })

        payload = {
            "notaModelo": self.nota_modelo,
            "codigoCliente": self.codigo_cliente_referencia,
            "produtos": produtos_payload,
        }

        if self.codigo_empresa:
            payload["codigoEmpresa"] = self.codigo_empresa


        resposta = self.fiscal_client.calcular_impostos(payload)

        produtos = resposta.get("produtos", [])

        if len(produtos) != len(itens):
            raise ValueError(
                f"Quantidade de produtos no cálculo fiscal difere dos itens do pedido: "
                f"{len(produtos)} != {len(itens)}"
            )

        compensacoes = []

        for item, produto_calc in zip(itens, produtos):
            quantidade = Decimal(str(item["quantidade"]))
            valor_unitario = Decimal(str(item["valorUnitario"]))
            valor_total_item = money_2(quantidade * valor_unitario)

            aliquota_ipi = self._extrair_aliquota_ipi(produto_calc)
            valor_desconto = self._calcular_desconto_compensatorio(
                valor_total_item=valor_total_item,
                aliquota_ipi=aliquota_ipi,
            )

            compensacao = {
                "sequencia": item["sequencia"],
                "codigoProduto": item["codigoProduto"],
                "aliquotaIpi": float(aliquota_ipi),
                "valorDesconto": float(valor_desconto),
                "percentualDesconto": float(aliquota_ipi),
            }

            if self.logger:
                self.logger.info(
                    "IPI item seq=%s cod=%s total=%.2f aliquota=%.4f desconto=%.2f",
                    item["sequencia"],
                    item["codigoProduto"],
                    float(valor_total_item),
                    float(aliquota_ipi),
                    float(valor_desconto),
                )

            compensacoes.append(compensacao)

        return compensacoes