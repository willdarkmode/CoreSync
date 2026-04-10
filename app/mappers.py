from app.utils import somente_digitos


class ProdutoMapper:
    def sku_wake_para_codigo_sankhya(self, sku: str) -> int:
        digits = somente_digitos(sku)
        return int(digits.lstrip("0") or "0")


class PagamentoMapper:
    def __init__(self, tipo_pagamento_padrao: int):
        self.tipo_pagamento_padrao = tipo_pagamento_padrao

    def obter_numero_parcelas(self, pagamento: dict | None) -> int:
        if not pagamento:
            return 1
        try:
            numero = int(pagamento.get("numeroParcelas", 1) or 1)
            return max(numero, 1)
        except (TypeError, ValueError):
            return 1

    def eh_pix(self, pagamento: dict | None) -> bool:
        if not pagamento:
            return False

        if pagamento.get("pix"):
            return True

        infos = pagamento.get("informacoesAdicionais") or []
        texto = " ".join(
            str(i.get("valor") or "") for i in infos
        ).lower()

        return "pix" in texto

    def eh_boleto(self, pagamento: dict | None) -> bool:
        if not pagamento:
            return False

        if pagamento.get("boleto"):
            return True

        infos = pagamento.get("informacoesAdicionais") or []
        texto = " ".join(
            str(i.get("valor") or "") for i in infos
        ).lower()

        return "boleto" in texto

    def eh_cartao(self, pagamento: dict | None) -> bool:
        if not pagamento:
            return False

        if self.eh_pix(pagamento) or self.eh_boleto(pagamento):
            return False

        infos = pagamento.get("informacoesAdicionais") or []
        texto = " ".join(
            str(i.get("valor") or "") for i in infos
        ).lower()

        return "cartão" in texto or "cartao" in texto

    def obter_tipo_pagamento(self, pedido_wake: dict, pagamento: dict | None = None) -> int:
        return self.tipo_pagamento_padrao

    def primeira_parcela_no_proximo_mes(self, pagamento: dict | None = None) -> bool:
        return self.eh_cartao(pagamento)