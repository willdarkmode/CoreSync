from app.services.normalizer import normalizar_pedido_wake


class PayloadBuilder:
    def __init__(
        self,
        ibge_service,
        produto_mapper,
        pagamento_mapper,
        codigo_local_estoque: int,
        nota_modelo: int,
        codigo_vendedor: int,
        cnpj_service=None,
        logger=None,
        zerar_ipi_itens: bool = False,
    ):
        self.ibge_service = ibge_service
        self.produto_mapper = produto_mapper
        self.pagamento_mapper = pagamento_mapper
        self.codigo_local_estoque = codigo_local_estoque
        self.nota_modelo = nota_modelo
        self.codigo_vendedor = codigo_vendedor
        self.cnpj_service = cnpj_service
        self.logger = logger
        self.zerar_ipi_itens = zerar_ipi_itens

    def montar(self, pedido_wake: dict) -> dict:
        pedido_norm = normalizar_pedido_wake(
            pedido_wake=pedido_wake,
            codigo_local_estoque=self.codigo_local_estoque,
            produto_mapper=self.produto_mapper,
            pagamento_mapper=self.pagamento_mapper,
            cnpj_service=self.cnpj_service,
            logger=self.logger,
            zerar_ipi_itens=self.zerar_ipi_itens,
        )

        cidade = pedido_norm["cliente"]["endereco"]["cidade"]
        uf = pedido_norm["cliente"]["endereco"]["uf"]
        codigo_ibge = self.ibge_service.obter_codigo_ibge(cidade, uf)

        # 👇 NOVO BLOCO: filtrar itens corretamente
        itens_payload = []
        for item in pedido_norm["itens"]:
            item_payload = {
                "sequencia": item["sequencia"],
                "codigoProduto": item["codigoProduto"],
                "quantidade": item["quantidade"],
                "controle": item["controle"],
                "codigoLocalEstoque": item["codigoLocalEstoque"],
                "valorUnitario": item["valorUnitario"],
            }

            # 👇 importante: só inclui se existir
            if item.get("impostos"):
                item_payload["impostos"] = item["impostos"]

            itens_payload.append(item_payload)

        payload = {
            "cliente": {
                "atualizar": pedido_norm["cliente"]["atualizar"],
                "tipo": pedido_norm["cliente"]["tipo"],
                "razao": pedido_norm["cliente"]["nome"],
                "endereco": {
                    "logradouro": pedido_norm["cliente"]["endereco"]["logradouro"],
                    "numero": pedido_norm["cliente"]["endereco"]["numero"],
                    "complemento": pedido_norm["cliente"]["endereco"]["complemento"],
                    "bairro": pedido_norm["cliente"]["endereco"]["bairro"],
                    "cidade": pedido_norm["cliente"]["endereco"]["cidade"],
                    "codigoIbge": codigo_ibge,
                    "uf": pedido_norm["cliente"]["endereco"]["uf"],
                    "cep": pedido_norm["cliente"]["endereco"]["cep"],
                },
                "cnpjCpf": pedido_norm["cliente"]["cnpjCpf"],
                "ieRg": pedido_norm["cliente"]["ieRg"],
                "nome": pedido_norm["cliente"]["nome"],
                "email": pedido_norm["cliente"]["email"],
                "telefoneNumero": pedido_norm["cliente"]["telefoneNumero"],
                "telefoneDdd": pedido_norm["cliente"]["telefoneDdd"],
                "AD_ECOMMERCE": "S",
            },
            "notaModelo": self.nota_modelo,
            "data": pedido_norm["data"],
            "hora": pedido_norm["hora"],
            "codigoVendedor": self.codigo_vendedor,
            "valorTotal": pedido_norm["valorTotal"],
            "AD_ECOMMERCE": "S",
            "itens": itens_payload,
            "financeiros": pedido_norm["financeiros"],
        }

        return payload, pedido_norm