"""
IVIS - Sprint 3: individualização dos cartuchos (OpenCV clássico)

Checklist do Trello -> funções:
  2. leitura de imagem ............ captura.da_pasta()
  3. pré-processamento ............ preprocessar()
  4. detecção de borda / ROI ...... detectar_cartuchos() + desenhar()
  5. recorte individual ........... recortar()
  6. armazenamento temporário ..... salvar_recorte()
  7. integração com a IHM ......... registrar() -> saida/resultados.json

Uso:
    python individualizacao.py pasta_de_imagens/ [pasta_de_saida/]

Depois de rodar, ABRA as imagens em saida/debug/ para conferir se os retângulos
estão certos. Se pegar ruído ou perder cartuchos, ajuste AREA_MIN e o kernel.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from captura import da_pasta

AREA_MIN = 0.02  # fração mínima da imagem para um contorno contar como cartucho


def preprocessar(img: np.ndarray) -> np.ndarray:
    """Cinza -> filtro gaussiano -> Otsu. Devolve máscara com o cartucho em branco."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # se a borda da imagem ficou branca, o fundo foi tratado como objeto: inverte
    borda = np.concatenate([mask[0], mask[-1], mask[:, 0], mask[:, -1]])
    if borda.mean() > 127:
        mask = cv2.bitwise_not(mask)

    # fecha buracos causados por texto/código de barras dentro do cartucho
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def detectar_cartuchos(img: np.ndarray) -> list[tuple]:
    """Bounding boxes (x1, y1, x2, y2) de cada cartucho, da esquerda para a direita."""
    mask = preprocessar(img)
    contornos, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = img.shape[:2]
    caixas = []
    for c in contornos:
        if cv2.contourArea(c) < AREA_MIN * h * w:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        caixas.append((x, y, x + bw, y + bh))
    return sorted(caixas, key=lambda b: b[0])


def desenhar(img: np.ndarray, caixas: list[tuple]) -> np.ndarray:
    out = img.copy()
    for k, (x1, y1, x2, y2) in enumerate(caixas, start=1):
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 3)
        cv2.putText(out, str(k), (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    return out


def recortar(img: np.ndarray, caixa: tuple, pad: float = 0.03) -> np.ndarray:
    x1, y1, x2, y2 = caixa
    h, w = img.shape[:2]
    px, py = int((x2 - x1) * pad), int((y2 - y1) * pad)
    return img[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]


def salvar_recorte(recorte: np.ndarray, pasta: Path, numero: int, indice: int) -> str:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"item{numero:03d}_{indice}.png"
    cv2.imwrite(str(caminho), recorte)
    return caminho.as_posix()


def registrar(numero: int, origem: str, indice, caixa, recorte_path) -> dict:
    """Registro que a IHM vai consumir (um por cartucho individualizado)."""
    return {
        "item": numero,
        "origem": origem,
        "indice": indice,
        "caixa": list(caixa) if caixa else None,
        "recorte": recorte_path,
        "capturado_em": datetime.now().isoformat(timespec="seconds"),
        "status": "individualizado" if recorte_path else "cartucho_ausente",
    }


if __name__ == "__main__":
    pasta_entrada = sys.argv[1]
    saida = Path(sys.argv[2] if len(sys.argv) > 2 else "saida")
    (saida / "debug").mkdir(parents=True, exist_ok=True)

    registros = []
    for item in da_pasta(pasta_entrada):
        caixas = detectar_cartuchos(item.imagem)
        cv2.imwrite(str(saida / "debug" / f"item{item.numero:03d}.png"), desenhar(item.imagem, caixas))

        if not caixas:
            registros.append(registrar(item.numero, item.origem, None, None, None))
            print(f"item {item.numero:03d} ({item.origem}): nenhum cartucho encontrado")
            continue

        for k, caixa in enumerate(caixas, start=1):
            caminho = salvar_recorte(recortar(item.imagem, caixa), saida / "recortes", item.numero, k)
            registros.append(registrar(item.numero, item.origem, k, caixa, caminho))
            print(f"item {item.numero:03d} ({item.origem}): cartucho {k} -> {caminho}")

    with open(saida / "resultados.json", "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)
    print(f"\n{len(registros)} registro(s) salvo(s) em {saida / 'resultados.json'}")
