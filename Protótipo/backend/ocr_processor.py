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
import numpy as np

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


def _ler_imagem(caminho: str | Path, flags=cv2.IMREAD_COLOR):
    """Lê imagem aceitando acentos no caminho (cv2.imread não garante no Windows)."""
    try:
        dados = np.fromfile(str(caminho), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(dados, flags) if dados.size else None


def avaliar_contraste_imagem(caminho_imagem: str | Path) -> bool:
    imagem = _ler_imagem(caminho_imagem, cv2.IMREAD_GRAYSCALE)
    if imagem is None:
        return False
    return cv2.Laplacian(imagem, cv2.CV_64F).var() < 100.0


def _normalizar_rotulo(texto: str) -> str:
    return re.sub(r"[^A-ZÀ-Ü]", "", texto.upper())


def _chave_data(data: str) -> tuple[int, int, int]:
    """Converte datas reconhecidas em chave cronológica para o fallback."""
    valor = data.upper().replace(" ", "")
    meses = {
        "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
        "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
    }
    m = re.fullmatch(r"([A-Z]{3})[/\-]?(\d{2,4})", valor)
    if m and m.group(1) in meses:
        ano = int(m.group(2))
        if ano < 100:
            ano += 2000 if ano < 70 else 1900
        return ano, meses[m.group(1)], 1
    m = re.fullmatch(r"(\d{2})/(\d{4})", valor)
    if m:
        return int(m.group(2)), int(m.group(1)), 1
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", valor)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    return 9999, 12, 31


def _rotulo_data(texto: str) -> str | None:
    """Identifica F/FAB ou V/VAL mesmo quando o OCR junta rótulo e data."""
    texto = texto.upper().replace("Ç", "C")
    if re.search(r"(?:^|[^A-Z])(?:F|FAB|FABR|FABRICACAO)(?=$|[^A-Z])", texto):
        return "fabricacao"
    if re.search(r"(?:^|[^A-Z])(?:V|VAL|VALIDADE|VENC)(?=$|[^A-Z])", texto):
        return "validade"
    return None


def extrair_dados_cartucho(textos_detectados: list[str]) -> tuple[str | None, str | None, str | None]:
    """Extrai lote, fabricação e validade priorizando os rótulos impressos F e V.

    O fallback cronológico só é usado quando o OCR não capturou os rótulos:
    a data mais antiga tende a ser a fabricação e a mais recente, a validade.
    """
    lote: str | None = None
    fabricacao: str | None = None
    validade: str | None = None
    datas_sem_rotulo: list[str] = []

    for i, texto in enumerate(textos_detectados):
        limpo = texto.strip().upper()
        compacto = re.sub(r"\s+", "", limpo)

        # Lote: não inclui o L isolado do rótulo; guarda somente o código numérico.
        candidatos_lote = re.findall(
            r"(?<![A-Z0-9])[A-Z]?[A-Z0-9-]{5,11}(?![A-Z0-9])",
            compacto
        )
        for candidato in candidatos_lote:
            candidato = candidato.strip("-:;.,")
            if candidato.startswith("L") and candidato[1:].isdigit():
                candidato = candidato[1:]
            if _PADRAO_LOTE.fullmatch(candidato) and candidato.upper() not in _ROTULOS:
                if (any(ch.isalpha() for ch in candidato) and any(ch.isdigit() for ch in candidato)) or (
                    candidato.isdigit() and 5 <= len(candidato) <= 8
                ):
                    lote = lote or candidato
                    break

        datas = [m.group(0).replace(" ", "").upper() for m in _PADRAO_DATA.finditer(limpo)]
        if not datas:
            continue

        rotulo = _rotulo_data(limpo)
        # Alguns leitores retornam o rótulo (F/V) em uma caixa e a data na próxima.
        if rotulo is None and i > 0:
            rotulo = _rotulo_data(textos_detectados[i - 1])
        if rotulo is None and i + 1 < len(textos_detectados):
            rotulo = _rotulo_data(textos_detectados[i + 1])

        for data in datas:
            if rotulo == "fabricacao":
                fabricacao = fabricacao or data
            elif rotulo == "validade":
                validade = validade or data
            else:
                datas_sem_rotulo.append(data)

    # Se os rótulos não foram lidos, usa a cronologia, não a ordem das caixas OCR.
    distintas = list(dict.fromkeys(datas_sem_rotulo))
    distintas.sort(key=_chave_data)
    if distintas:
        if not fabricacao:
            fabricacao = distintas[0]
        if not validade:
            candidatas = [d for d in distintas if d != fabricacao]
            if candidatas:
                validade = candidatas[-1]

    return lote, fabricacao, validade



# ---------------------------------------------------------------------------
# Leitura por linhas (etapa 4).
# Testes de 2026-11-25 com 14 recortes de vídeo mostraram que o EasyOCR lê o
# rótulo F como "6"/"€"/"E" e V como "Y"/"4"; a busca do rótulo no texto
# vizinho então ligava o "V" da linha seguinte à data de fabricação. Aqui os
# textos são agrupados por linha (posição vertical) e cada rótulo só vale para
# a própria linha.
# ---------------------------------------------------------------------------
OCR_ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/-."
_MESES_LISTA = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
                "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
_ALIAS_FAB = {"F", "FAB", "FABR", "E", "6", "€", "P"}
_ALIAS_VAL = {"V", "VAL", "Y", "4", "U", "W"}
# Trocas típicas do OCR nesta impressão (mês só tem letras).
_DIGITO_LETRA = str.maketrans({"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S",
                               "6": "G", "7": "T", "8": "B"})
_CONFUSOES = {frozenset(p) for p in (("M", "N"), ("M", "H"), ("M", "K"), ("M", "W"),
                                      ("R", "P"), ("V", "Y"), ("V", "U"), ("G", "C"),
                                      ("O", "D"), ("O", "Q"), ("E", "F"), ("I", "L"),
                                      ("T", "I"))}
# Separador obrigatório: sem ele, um lote como 100648 parecia "006/48".
_PADRAO_DATA_LINHA = re.compile(r"([A-Z0-9€]{3})[/\-.](\d{2}(?:\d{2})?)$")


def _custo_mes(lido: str, mes: str) -> float:
    custo = 0.0
    for a, b in zip(lido, mes):
        if a != b:
            custo += 0.5 if frozenset((a, b)) in _CONFUSOES else 1.0
    return custo


def corrigir_mes(lido: str) -> str | None:
    """Aproxima 3 caracteres lidos de um mês válido, só quando não há dúvida.

    Aceita no máximo custo 1,0 (ex.: 5E7->SET, A60->AGO, HAR->MAR, KAR->MAR)
    e exige que o segundo mês mais próximo fique ao menos 0,5 mais distante.
    Caso contrário devolve None: o campo fica "Não identificado".
    """
    lido = lido.upper().translate(_DIGITO_LETRA)
    if lido in _MESES_LISTA:
        return lido
    custos = sorted((_custo_mes(lido, m), m) for m in _MESES_LISTA)
    (c1, m1), (c2, _m2) = custos[0], custos[1]
    if c1 <= 1.0 and c2 - c1 >= 0.5:
        return m1
    return None


def corrigir_data(data: str) -> str | None:
    m = re.fullmatch(r"([A-Z0-9€]{3})[/\-.]?(\d{2}(?:\d{2})?)", data.upper().replace(" ", ""))
    if not m:
        return data  # formatos numéricos (01/2027) passam sem alteração
    mes = corrigir_mes(m.group(1))
    return f"{mes}/{m.group(2)}" if mes else None


def _agrupar_linhas(resultados) -> list[list[str]]:
    """Agrupa caixas do EasyOCR em linhas, de cima para baixo e da esquerda para a direita."""
    itens = []
    for bbox, texto, prob in resultados:
        texto = texto.strip()
        if not texto or prob <= 0.0:
            continue
        ys = [p[1] for p in bbox]
        xs = [p[0] for p in bbox]
        itens.append({"yc": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys),
                      "x": min(xs), "texto": texto})
    itens.sort(key=lambda i: i["yc"])
    linhas: list[list[dict]] = []
    for item in itens:
        if linhas:
            ultima = linhas[-1]
            ref = sum(i["yc"] for i in ultima) / len(ultima)
            alt = max(max(i["h"] for i in ultima), item["h"])
            if abs(item["yc"] - ref) <= 0.5 * alt:
                ultima.append(item)
                continue
        linhas.append([item])
    return [[i["texto"] for i in sorted(l, key=lambda i: i["x"])] for l in linhas]


def _rotulo_e_valor(tokens: list[str]) -> tuple[str, str]:
    """Separa o rótulo (L/F/V e variações lidas) do valor da linha."""
    tokens = [t.upper().replace(" ", "") for t in tokens]
    if len(tokens) > 1 and len(tokens[0]) <= 3 and not _PADRAO_DATA_LINHA.search(tokens[0]):
        return tokens[0], "".join(tokens[1:])
    junto = "".join(tokens)
    m = _PADRAO_DATA_LINHA.search(junto)
    if m and m.start() > 0:  # rótulo colado na data: "YA60/27", "FMAR/26"
        return junto[:m.start()], junto[m.start():]
    return "", junto


def extrair_por_linhas(resultados) -> tuple[str | None, str | None, str | None]:
    lote = fabricacao = validade = None
    datas_sem_rotulo: list[str] = []

    for tokens in _agrupar_linhas(resultados):
        rotulo, valor = _rotulo_e_valor(tokens)
        m = _PADRAO_DATA_LINHA.search(valor)
        if m:
            mes = corrigir_mes(m.group(1))
            if mes is None:
                continue
            data = f"{mes}/{m.group(2)}"
            if rotulo in _ALIAS_FAB and not fabricacao:
                fabricacao = data
            elif rotulo in _ALIAS_VAL and not validade:
                validade = data
            else:
                datas_sem_rotulo.append(data)
            continue

        if lote is None:
            candidato = valor.strip("-:;.,")
            if candidato.startswith("L") and candidato[1:].isdigit():
                candidato = candidato[1:]
            if _PADRAO_LOTE.fullmatch(candidato) and any(ch.isdigit() for ch in candidato):
                lote = candidato

    # Sem rótulo legível: a data mais antiga é a fabricação, a mais nova a validade.
    distintas = sorted(dict.fromkeys(datas_sem_rotulo), key=_chave_data)
    if distintas:
        if not fabricacao and not (validade and _chave_data(distintas[0]) >= _chave_data(validade)):
            fabricacao = distintas.pop(0)
        if not validade and distintas:
            validade = distintas[-1]

    return lote, fabricacao, validade


def votar_campos(leituras: list[dict[str, Any]]) -> dict[str, Any]:
    """Combina leituras OCR de vários quadros da MESMA embalagem.

    leituras[0] é o melhor quadro e decide, porque os quadros extras têm
    qualidade menor (testado em 2026-11-25: votação simples piorou de 37 para
    33 campos corretos em 42). Os extras só substituem o valor quando pelo
    menos dois deles concordam entre si e somam mais votos que o melhor quadro.
    Quando há valores diferentes entre quadros, o campo entra em
    "divergentes" para gerar aviso. Nenhum valor é criado.
    """
    resultado: dict[str, Any] = {"votos": {}, "divergentes": []}
    for campo in ("lote", "fabricacao", "validade"):
        principal = leituras[0].get(campo)
        valores = [l.get(campo) for l in leituras if l.get(campo)]
        contagem = Counter(valores)
        resultado["votos"][campo] = dict(contagem.most_common())
        escolhido = principal
        if contagem:
            mais_votado, votos = contagem.most_common(1)[0]
            if votos >= 2 and votos > contagem.get(principal, 0):
                escolhido = mais_votado
        resultado[campo] = escolhido
        if len(contagem) > 1:
            resultado["divergentes"].append(campo)
    return resultado


def validar_campos_obrigatorios(lote, fabricacao, validade):
    """Erros bloqueantes: somente quando algum campo não foi identificado."""
    faltantes = [
        nome
        for nome, valor in (
            ("Lote", lote),
            ("Fabricação", fabricacao),
            ("Validade", validade),
        )
        if not valor
    ]

    if not faltantes:
        return []

    return [{
        "codigo": "E-MF-02",
        "descricao": "Campo obrigatório não identificado: "
                     + ", ".join(faltantes) + "."
    }]


def avaliar_qualidade_leitura(resultados_ocr, caminho_imagem):
    """Avisos de qualidade que não reprovam a leitura dos campos."""
    avisos = []

    if avaliar_contraste_imagem(caminho_imagem):
        avisos.append({
            "codigo": "A-LC-01",
            "descricao": (
                "Baixo contraste ou nitidez insuficiente. "
                "Confira visualmente a leitura."
            )
        })

    for _bbox, texto, probabilidade in resultados_ocr:
        texto_limpo = _normalizar_rotulo(texto.strip())

        if not texto_limpo or texto_limpo in _ROTULOS:
            continue

        confianca = float(probabilidade)

        if 0.0 < confianca < 0.60:
            avisos.append({
                "codigo": "A-DC-03",
                "descricao": (
                    f"Baixa confiança no texto '{texto}' "
                    f"(Confiança: {confianca:.2f}). "
                    "Confira visualmente a leitura."
                )
            })
            break

    return avisos



def processar_etiqueta(caminho_imagem: str | Path) -> dict[str, Any]:
    """Executa OCR no recorte YOLO e devolve resultado pronto para a API."""
    caminho = Path(caminho_imagem)
    if not caminho.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {caminho}")

    imagem = _ler_imagem(caminho)
    if imagem is None:
        raise ValueError(f"Não foi possível abrir a imagem: {caminho}")

    # Aumenta recortes pequenos para ajudar o OCR sem alterar o arquivo original.
    altura, largura = imagem.shape[:2]
    if max(altura, largura) < 1400:
        escala = min(3.0, 1400 / max(altura, largura))
        imagem_ocr = cv2.resize(imagem, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    else:
        imagem_ocr = imagem

    resultados = obter_leitor().readtext(
        imagem_ocr, detail=1, paragraph=False, allowlist=OCR_ALLOWLIST
    )
    textos_validos = [texto.strip() for _bbox, texto, prob in resultados if texto.strip() and prob > 0.0]
    lote, fabricacao, validade = extrair_por_linhas(resultados)
    # Fallback para o extrator antigo apenas nos campos que ficaram vazios.
    if not (lote and fabricacao and validade):
        lote_a, fab_a, val_a = extrair_dados_cartucho(textos_validos)
        lote = lote or lote_a
        fabricacao = fabricacao or (corrigir_data(fab_a) if fab_a else None)
        validade = validade or (corrigir_data(val_a) if val_a else None)
        if fabricacao and validade and fabricacao == validade:
            validade = None
    erros = validar_campos_obrigatorios(lote, fabricacao, validade)
    avisos = avaliar_qualidade_leitura(resultados, caminho)

    if not erros and not gabarito_lote.gabarito_ativo:
        gabarito_lote.avaliar_sequencia(textos_validos)

    campos_identificados = len(erros) == 0

    return {
        "lote": lote,
        "fabricacao": fabricacao,
        "validade": validade,
        "textos_detectados": [
            {
                "texto": texto,
                "confianca": round(float(prob), 4)
            }
            for _bbox, texto, prob in resultados
        ],
        "erros": erros,
        "avisos": avisos,
        "aprovado": campos_identificados,
        "campos_identificados": campos_identificados,
        "status_leitura": "Concluída" if campos_identificados else "Incompleta",
        "qualidade_imagem": "Atenção" if avisos else "Adequada",
        "gabarito": gabarito_lote.gabarito_ativo,
    }


def processar_embalagem(caminhos: list[str | Path]) -> dict[str, Any]:
    """OCR de vários recortes da MESMA embalagem, combinados por votação.

    Devolve o mesmo formato de processar_etiqueta (compatível com app.py),
    com lote/fabricação/validade vindos da votação e os detalhes em
    "leituras" e "votos". Recortes de outras embalagens nunca entram aqui.
    """
    caminhos = [Path(c) for c in caminhos if c]
    if not caminhos:
        raise ValueError("Nenhum recorte informado para o OCR.")

    leituras = [processar_etiqueta(c) for c in caminhos]
    if len(leituras) == 1:
        leituras[0]["leituras"] = [{k: leituras[0][k] for k in ("lote", "fabricacao", "validade")}]
        return leituras[0]

    votacao = votar_campos(leituras)
    principal = dict(leituras[0])  # o recorte do melhor quadro é o de referência
    principal.update({k: votacao[k] for k in ("lote", "fabricacao", "validade")})
    principal["votos"] = votacao["votos"]
    if votacao["divergentes"]:
        nomes = {"lote": "Lote", "fabricacao": "Fabricação", "validade": "Validade"}
        principal["avisos"] = list(principal.get("avisos", [])) + [{
            "codigo": "A-OC-04",
            "descricao": "Leitura diferente entre quadros da mesma embalagem: "
                         + ", ".join(nomes[c] for c in votacao["divergentes"])
                         + ". Confira visualmente a leitura."
        }]
        principal["qualidade_imagem"] = "Atenção"
    principal["leituras"] = [
        {"arquivo": c.name, **{k: l[k] for k in ("lote", "fabricacao", "validade")}}
        for c, l in zip(caminhos, leituras)
    ]
    principal["erros"] = validar_campos_obrigatorios(
        principal["lote"], principal["fabricacao"], principal["validade"]
    )
    completos = not principal["erros"]
    principal["aprovado"] = completos
    principal["campos_identificados"] = completos
    principal["status_leitura"] = "Concluída" if completos else "Incompleta"
    return principal


# Alias para deixar explícito o uso futuro no endpoint da API.
processar_etiqueta_com_gabarito = processar_etiqueta
