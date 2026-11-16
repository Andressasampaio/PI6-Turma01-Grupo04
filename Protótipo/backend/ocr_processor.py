"""Processamento OCR das etiquetas/cartuchos do IVIS.

Recebe o recorte produzido pelo YOLO e devolve os campos lidos e os erros.
O EasyOCR e inicializado sob demanda para evitar carregar os modelos no import.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any

import cv2
import easyocr

_reader = None


def obter_leitor():
    """Cria o leitor apenas na primeira inspeção."""
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["pt", "en"], gpu=False)
    return _reader


class GerenciadorGabarito:
    """Mantém a validação estrutural por sequência do código original."""
    def __init__(self, tamanho_amostra: int = 3):
        self.tamanho_amostra = tamanho_amostra
        self.historico_amostras: list[int] = []
        self.gabarito_ativo: dict[str, Any] | None = None

    def avaliar_sequencia(self, textos_detectados: list[str]) -> bool:
        if self.gabarito_ativo:
            return True

        self.historico_amostras.append(len(textos_detectados))
        if len(self.historico_amostras) >= self.tamanho_amostra:
            contagem = Counter(self.historico_amostras)
            assinatura, frequencia = contagem.most_common(1)[0]
            if frequencia >= (self.tamanho_amostra / 2):
                self.gabarito_ativo = {
                    "assinatura_padrao": assinatura,
                    "status": "Definido por Sequência",
                }
            else:
                self.historico_amostras = []
        return self.gabarito_ativo is not None


# Uma instância por processo do backend.
gabarito_lote = GerenciadorGabarito(tamanho_amostra=3)

# Datas como JAN/27, JAN 2027, 01/2027 ou 31/01/2027.
_PADRAO_DATA = re.compile(
    r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[/\s-]*\d{2,4}\b"
    r"|\b\d{2}/\d{2}/\d{4}\b"
    r"|\b\d{2}/\d{4}\b",
    re.IGNORECASE,
)
_MESES = r"JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ"
_PADRAO_LOTE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9-]{4,11}$", re.IGNORECASE)
_ROTULOS = {"L", "LOTE", "F", "FAB", "FABR", "FABRICACAO", "FABRICAÇÃO",
            "V", "VAL", "VALIDADE", "VAL.", "DATA"}


def avaliar_contraste_imagem(caminho_imagem: str | Path) -> bool:
    imagem = cv2.imread(str(caminho_imagem), cv2.IMREAD_GRAYSCALE)
    if imagem is None:
        return False
    return cv2.Laplacian(imagem, cv2.CV_64F).var() < 100.0


def _normalizar_rotulo(texto: str) -> str:
    return re.sub(r"[^A-ZÀ-Ü]", "", texto.upper())


def extrair_dados_cartucho(textos_detectados: list[str]) -> tuple[str | None, str | None, str | None]:
    """Extrai lote, fabricação e validade; reconhece lotes alfanuméricos como L100648."""
    lote = None
    fabricacao = None
    validade = None
    datas_sem_rotulo: list[str] = []

    for i, texto in enumerate(textos_detectados):
        limpo = texto.strip().upper()
        compacto = re.sub(r"\s+", "", limpo)
        rotulo_atual = _normalizar_rotulo(limpo)
        rotulo_anterior = _normalizar_rotulo(textos_detectados[i - 1]) if i > 0 else ""

        # Captura lote na mesma caixa (ex.: LOTE L100648) ou em caixa isolada.
        candidatos_lote = re.findall(r"(?<![A-Z0-9])[A-Z]?[A-Z0-9-]{5,11}(?![A-Z0-9])", compacto)
        for candidato in candidatos_lote:
            candidato = candidato.strip("-:;.,")
            # O OCR pode separar o prefixo "L" do número do lote em duas caixas.
            if candidato.isdigit() and rotulo_anterior == "L":
                candidato = "L" + candidato
            if _PADRAO_LOTE.fullmatch(candidato) and candidato.upper() not in _ROTULOS:
                if any(ch.isalpha() for ch in candidato) and any(ch.isdigit() for ch in candidato):
                    lote = candidato
                    break
                if candidato.isdigit() and 5 <= len(candidato) <= 8:
                    lote = candidato
                    break
        if lote:
            # Não interrompe: ainda precisamos encontrar as datas.
            pass

        datas = [m.group(0).replace(" ", "").upper() for m in _PADRAO_DATA.finditer(limpo)]
        if not datas:
            continue

        contexto = f"{rotulo_anterior} {rotulo_atual}"
        for data in datas:
            if rotulo_anterior in {"F", "FAB", "FABR", "FABRICACAO"} or any(
                tag in contexto for tag in ("FAB", "FABRICACAO", "DATAFAB")
            ):
                fabricacao = fabricacao or data
            elif rotulo_anterior in {"V", "VAL", "VALIDADE"} or any(
                tag in contexto for tag in ("VAL", "VALIDADE", "VENC")
            ):
                validade = validade or data
            else:
                datas_sem_rotulo.append(data)

    # Se o OCR não associou rótulos, usa a ordem de aparição como fallback.
    distintas = list(dict.fromkeys(datas_sem_rotulo))
    if not fabricacao and distintas:
        fabricacao = distintas[0]
    if not validade:
        for data in distintas:
            if data != fabricacao:
                validade = data
                break
    return lote, fabricacao, validade


def validar_e_classificar_erros(
    resultados_ocr: list[tuple], caminho_imagem: str | Path,
    lote: str | None, fabricacao: str | None, validade: str | None,
) -> list[dict[str, str]]:
    erros: list[dict[str, str]] = []
    if avaliar_contraste_imagem(caminho_imagem):
        erros.append({"codigo": "E-LC-01", "descricao": "Baixo contraste ou nitidez insuficiente na imagem da etiqueta."})
    if not lote or not fabricacao or not validade:
        faltantes = [nome for nome, valor in (("Lote", lote), ("Fabricação", fabricacao), ("Validade", validade)) if not valor]
        erros.append({"codigo": "E-MF-02", "descricao": "Campo obrigatório não identificado: " + ", ".join(faltantes) + "."})

    for _bbox, texto, probabilidade in resultados_ocr:
        texto_limpo = _normalizar_rotulo(texto.strip())
        if not texto_limpo or texto_limpo in _ROTULOS:
            continue
        if 0.0 < float(probabilidade) < 0.60:
            erros.append({
                "codigo": "E-DC-03",
                "descricao": f"Baixa confiança da IA no texto '{texto}' (Confiança: {float(probabilidade):.2f}).",
            })
            break
    return erros


def processar_etiqueta(caminho_imagem: str | Path) -> dict[str, Any]:
    """Executa OCR no recorte YOLO e devolve resultado pronto para a API."""
    caminho = Path(caminho_imagem)
    if not caminho.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {caminho}")

    imagem = cv2.imread(str(caminho))
    if imagem is None:
        raise ValueError(f"Não foi possível abrir a imagem: {caminho}")

    # Aumenta recortes pequenos para ajudar o OCR sem alterar o arquivo original.
    altura, largura = imagem.shape[:2]
    if max(altura, largura) < 1400:
        escala = min(3.0, 1400 / max(altura, largura))
        imagem_ocr = cv2.resize(imagem, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    else:
        imagem_ocr = imagem

    resultados = obter_leitor().readtext(imagem_ocr, detail=1, paragraph=False)
    textos_validos = [texto.strip() for _bbox, texto, prob in resultados if texto.strip() and prob > 0.0]
    lote, fabricacao, validade = extrair_dados_cartucho(textos_validos)
    erros = validar_e_classificar_erros(resultados, caminho, lote, fabricacao, validade)

    if not erros and not gabarito_lote.gabarito_ativo:
        gabarito_lote.avaliar_sequencia(textos_validos)

    return {
        "lote": lote,
        "fabricacao": fabricacao,
        "validade": validade,
        "textos_detectados": [
            {"texto": texto, "confianca": round(float(prob), 4)}
            for _bbox, texto, prob in resultados
        ],
        "erros": erros,
        "aprovado": len(erros) == 0,
        "gabarito": gabarito_lote.gabarito_ativo,
    }


# Alias para deixar explícito o uso futuro no endpoint da API.
processar_etiqueta_com_gabarito = processar_etiqueta
