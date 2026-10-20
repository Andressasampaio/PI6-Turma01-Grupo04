"""
IVIS - Sprint 3: captura de imagens na esteira (RF01) + individualização dos itens

- da_pasta(): simula a esteira lendo uma pasta de imagens (arquivos inválidos são ignorados com aviso)
- da_camera(): lê frames de uma câmera
- individualizar(): roda o YOLO e devolve UMA detecção por etiqueta encontrada
  (lista vazia = nenhuma etiqueta -> "etiqueta ausente", conforme RF02)

Uso de teste:
    python captura.py best.pt pasta_de_imagens/
"""
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

EXTENSOES = {".jpg", ".jpeg", ".png"}


@dataclass
class Item:
    numero: int
    origem: str
    imagem: np.ndarray


@dataclass
class Deteccao:
    item: int
    indice: int          # posição da etiqueta dentro da imagem (esquerda -> direita)
    confianca: float
    caixa: tuple         # (x1, y1, x2, y2) na imagem original
    recorte: np.ndarray


def da_pasta(pasta: str, intervalo: float = 0.0) -> Iterator[Item]:
    """Entrega uma imagem por vez, numerando os itens. `intervalo` simula a velocidade da esteira."""
    numero = 0
    for caminho in sorted(Path(pasta).iterdir()):
        if caminho.suffix.lower() not in EXTENSOES:
            print(f"[aviso] ignorado (extensão inválida): {caminho.name}")
            continue
        img = cv2.imread(str(caminho))
        if img is None:
            print(f"[aviso] ignorado (não abriu): {caminho.name}")
            continue
        numero += 1
        yield Item(numero, caminho.name, img)
        if intervalo:
            time.sleep(intervalo)


def da_camera(indice: int = 0) -> Iterator[Item]:
    cap = cv2.VideoCapture(indice)
    numero = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            numero += 1
            yield Item(numero, f"camera:{indice}", frame)
    finally:
        cap.release()


def individualizar(model, item: Item, conf: float = 0.4, pad: float = 0.05) -> list[Deteccao]:
    """Uma Deteccao por etiqueta, ordenadas da esquerda para a direita."""
    r = model.predict(item.imagem, conf=conf, verbose=False)[0]
    h, w = item.imagem.shape[:2]
    caixas = sorted(
        zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist()), key=lambda c: c[0][0]
    )
    saida = []
    for k, (xyxy, c) in enumerate(caixas, start=1):
        x1, y1, x2, y2 = map(int, xyxy)
        px, py = int((x2 - x1) * pad), int((y2 - y1) * pad)
        recorte = item.imagem[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]
        saida.append(Deteccao(item.numero, k, float(c), (x1, y1, x2, y2), recorte))
    return saida


if __name__ == "__main__":
    from ultralytics import YOLO

    pesos, pasta = sys.argv[1], sys.argv[2]
    modelo = YOLO(pesos)
    destino = Path("saida_recortes")
    destino.mkdir(exist_ok=True)

    for item in da_pasta(pasta):
        deteccoes = individualizar(modelo, item)
        if not deteccoes:
            print(f"item {item.numero:03d} ({item.origem}): ETIQUETA AUSENTE")
            continue
        for d in deteccoes:
            arquivo = destino / f"item{item.numero:03d}_{d.indice}.png"
            cv2.imwrite(str(arquivo), d.recorte)
            print(f"item {item.numero:03d} ({item.origem}): etiqueta {d.indice} conf={d.confianca:.2f} -> {arquivo}")
