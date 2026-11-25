"""Gabarito automático do IVIS (etapa 5).

Regra de escopo: cada vídeo enviado é um PROCESSO de inspeção, e cada
processo tem o seu próprio gabarito. Um gabarito nunca é reaproveitado
em outro processo.

Definição: as três primeiras embalagens válidas do processo (ordem de entrada
na cena) são as referências. Um campo só é definido automaticamente quando as
três leituras concordam. Qualquer divergência deixa o gabarito "Pendente de
confirmação" e o operador precisa confirmar o valor correto. Nenhum valor é
escolhido por maioria ou ao acaso.
"""
from __future__ import annotations

import re
import unicodedata

CAMPOS = ("lote", "fabricacao", "validade")
NOMES_CAMPOS = {"lote": "Lote", "fabricacao": "Fabricação", "validade": "Validade"}
TOTAL_REFERENCIAS = 3

STATUS_DEFINIDO = "Definido"
STATUS_PENDENTE = "Pendente de confirmação"
ORIGEM_AUTOMATICA = "Automático (3 primeiras embalagens)"
ORIGEM_MANUAL = "Confirmado pelo operador"

STATUS_AGUARDANDO = "Aguardando gabarito"


def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def normalizar_valor(campo: str, valor) -> str:
    """Forma canônica para comparação (não altera o valor exibido)."""
    texto = _sem_acento(str(valor or "")).upper()
    texto = "".join(texto.split())
    if texto in ("", "NAOIDENTIFICADO", "NAOLEGIVEL", "NONE"):
        return ""
    if campo == "lote":
        if texto.startswith("L") and texto[1:].isdigit():
            texto = texto[1:]
        return texto
    m = re.fullmatch(r"([A-Z]{3})/(\d{2}|\d{4})", texto)
    if m:  # MAR/26 e MAR/2026 são a mesma data
        ano = m.group(2) if len(m.group(2)) == 4 else "20" + m.group(2)
        return f"{m.group(1)}/{ano}"
    return texto


def leitura_valida(leitura: dict) -> bool:
    """Embalagem apta a ser referência: os três campos lidos."""
    return all(normalizar_valor(c, leitura.get(c)) for c in CAMPOS)


def definir_gabarito(embalagens: list[dict]) -> dict:
    """Define o gabarito a partir das embalagens em ordem de entrada.

    embalagens: [{"chave": <id da embalagem>, "lote", "fabricacao", "validade"}]
    Devolve status, valores (None nos campos não definidos), referências
    usadas e divergências por campo.
    """
    referencias = [e for e in embalagens if leitura_valida(e)][:TOTAL_REFERENCIAS]
    resultado = {
        "status": STATUS_PENDENTE,
        "origem": ORIGEM_AUTOMATICA,
        "lote": None, "fabricacao": None, "validade": None,
        "referencias": [e["chave"] for e in referencias],
        "leituras_referencia": [
            {"chave": e["chave"], **{c: e.get(c) for c in CAMPOS}} for e in referencias
        ],
        "divergencias": [],
        "motivo": "",
    }

    if len(referencias) < TOTAL_REFERENCIAS:
        resultado["motivo"] = (
            f"Apenas {len(referencias)} embalagem(ns) com leitura completa; "
            f"são necessárias {TOTAL_REFERENCIAS} para definir o gabarito."
        )
        return resultado

    for campo in CAMPOS:
        normalizados = {normalizar_valor(campo, e[campo]) for e in referencias}
        if len(normalizados) == 1:
            resultado[campo] = referencias[0][campo]
        else:
            resultado["divergencias"].append({
                "campo": campo,
                "valores": [e[campo] for e in referencias],
            })

    if resultado["divergencias"]:
        nomes = ", ".join(NOMES_CAMPOS[d["campo"]] for d in resultado["divergencias"])
        resultado["motivo"] = f"Divergência entre as 3 primeiras embalagens em: {nomes}."
    else:
        resultado["status"] = STATUS_DEFINIDO
        resultado["motivo"] = "As 3 primeiras embalagens concordam nos três campos."
    return resultado


def classificar(leitura: dict, gabarito: dict | None) -> tuple[str, str]:
    """Compara uma embalagem com o gabarito do SEU processo."""
    if not gabarito or gabarito.get("status") != STATUS_DEFINIDO:
        return STATUS_AGUARDANDO, "Gabarito do processo pendente de confirmação pelo operador"

    faltantes = [NOMES_CAMPOS[c] for c in CAMPOS if not normalizar_valor(c, leitura.get(c))]
    if faltantes:
        return "Não Conforme", "Leitura OCR incompleta: " + ", ".join(faltantes) + " não identificado(s)"

    divergencias = []
    for campo in CAMPOS:
        if normalizar_valor(campo, leitura.get(campo)) != normalizar_valor(campo, gabarito.get(campo)):
            divergencias.append(
                f"{NOMES_CAMPOS[campo]} divergente: {leitura.get(campo)} (gabarito {gabarito.get(campo)})"
            )
    if divergencias:
        return "Não Conforme", "; ".join(divergencias)
    return "Conforme", "Lote, fabricação e validade correspondem ao gabarito"
