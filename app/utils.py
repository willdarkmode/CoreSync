import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def somente_digitos(valor) -> str:
    if valor is None:
        return ""
    return re.sub(r"\D", "", str(valor))


def normalizar_texto(txt: str) -> str:
    txt = str(txt or "").strip().lower()
    mapa = str.maketrans({
        "á": "a", "à": "a", "ã": "a", "â": "a",
        "é": "e", "ê": "e",
        "í": "i",
        "ó": "o", "ô": "o", "õ": "o",
        "ú": "u",
        "ç": "c",
    })
    return txt.translate(mapa)


def quebrar_telefone(telefone: str) -> tuple[str, str]:
    digits = somente_digitos(telefone)
    if len(digits) >= 10:
        return digits[:2], digits[2:]
    return "", digits


def parse_datetime_iso_flex(value: str) -> datetime:
    if not value:
        raise ValueError("Data/hora ausente.")

    value = value.strip()

    tentativas = [
        value,
        value.replace("Z", "+00:00"),
    ]

    for item in tentativas:
        try:
            return datetime.fromisoformat(item)
        except ValueError:
            continue

    formatos = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formatos:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    raise ValueError(f"Formato de data não suportado: {value}")


def formatar_data_hora_br(data_iso: str) -> tuple[str, str]:
    dt = parse_datetime_iso_flex(data_iso)
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")


def safe_float(valor, default: float = 0.0) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


def safe_int(valor, default: int = 0) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return default


def adicionar_meses(data: datetime, meses: int) -> datetime:
    mes = data.month - 1 + meses
    ano = data.year + mes // 12
    mes = mes % 12 + 1

    dias_por_mes = [
        31,
        29 if ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0) else 28,
        31, 30, 31, 30,
        31, 31, 30, 31, 30, 31
    ]

    dia = min(data.day, dias_por_mes[mes - 1])
    return data.replace(year=ano, month=mes, day=dia)


def to_decimal(valor, default: str = "0.00") -> Decimal:
    try:
        if valor is None or valor == "":
            return Decimal(default)
        return Decimal(str(valor).replace(",", "."))
    except Exception:
        return Decimal(default)


def money_2(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)