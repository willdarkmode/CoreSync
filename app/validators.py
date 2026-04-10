from app.exceptions import ValidacaoError


def validar_config(settings) -> None:
    faltantes = []

    if not settings.wake_auth:
        faltantes.append("WAKE_AUTH")
    if not settings.sankhya_x_token:
        faltantes.append("SANKHYA_X_TOKEN")
    if not settings.sankhya_client_id:
        faltantes.append("SANKHYA_CLIENT_ID")
    if not settings.sankhya_client_secret:
        faltantes.append("SANKHYA_CLIENT_SECRET")

    if faltantes:
        raise ValidacaoError(
            "Variáveis obrigatórias ausentes: " + ", ".join(faltantes)
        )


def validar_pedido_wake_bruto(pedido: dict) -> None:
    erros = []

    if not pedido.get("pedidoId"):
        erros.append("pedidoId ausente")

    if not pedido.get("data"):
        erros.append("data ausente")

    if not pedido.get("usuario"):
        erros.append("usuario ausente")

    itens = pedido.get("itens", [])
    if not itens:
        erros.append("pedido sem itens")

    if erros:
        raise ValidacaoError("Pedido Wake inválido: " + " | ".join(erros))


def validar_pedido_normalizado(pedido: dict) -> None:
    erros = []

    cliente = pedido.get("cliente", {})
    endereco = cliente.get("endereco", {})
    itens = pedido.get("itens", [])
    financeiros = pedido.get("financeiros", [])

    if not cliente.get("nome"):
        erros.append("cliente sem nome")

    if not cliente.get("cnpjCpf"):
        erros.append("cliente sem CPF/CNPJ")

    if not endereco.get("logradouro"):
        erros.append("logradouro ausente")

    if not endereco.get("cidade"):
        erros.append("cidade ausente")

    if not endereco.get("uf"):
        erros.append("UF ausente")

    if not itens:
        erros.append("pedido sem itens")

    for item in itens:
        if item.get("codigoProduto") in (None, "", 0):
            erros.append(f"item sem código do produto (SKU {item.get('skuOriginal')})")
        if float(item.get("quantidade", 0)) <= 0:
            erros.append(f"quantidade inválida (SKU {item.get('skuOriginal')})")
        if float(item.get("valorUnitario", -1)) < 0:
            erros.append(f"valor unitário inválido (SKU {item.get('skuOriginal')})")

    if not financeiros:
        erros.append("pedido sem financeiros")

    if erros:
        raise ValidacaoError("Pedido normalizado inválido: " + " | ".join(erros))