"""
IVIS - teste com webcam: o YOLO procura a etiqueta (lote/validade/código de barras)
da caixa de remédio ao vivo.

Instalação:
    pip install ultralytics opencv-python

Uso:
    python webcam_teste.py best.pt          # webcam padrão
    python webcam_teste.py best.pt 1        # outra webcam (índice 1)

Teclas na janela:
    q = sair
    t = alterna entre "esticar para 512x512" (como no treino) e "normal"
    s = salva a imagem e o recorte da etiqueta em saida_webcam/
"""
import sys
import time
from pathlib import Path

import cv2

TAM = 512  # tamanho usado no treino (Roboflow: Stretch to 512x512)
FONTE = cv2.FONT_HERSHEY_SIMPLEX


def detectar(model, frame, esticar: bool, conf: float = 0.4):
    """Devolve [(x1, y1, x2, y2, confianca)] já nas coordenadas do frame original."""
    h, w = frame.shape[:2]
    if esticar:
        # o modelo foi treinado com fotos esticadas para 512x512, então imitamos isso
        entrada = cv2.resize(frame, (TAM, TAM))
        sx, sy = w / TAM, h / TAM
    else:
        entrada, sx, sy = frame, 1.0, 1.0
    r = model.predict(entrada, imgsz=TAM, conf=conf, verbose=False)[0]
    caixas = []
    for (x1, y1, x2, y2), c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist()):
        caixas.append((int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), float(c)))
    return caixas


def main():
    from ultralytics import YOLO

    if len(sys.argv) < 2:
        sys.exit("Uso: python webcam_teste.py best.pt [indice_da_camera]")
    pesos = sys.argv[1]
    indice = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    modelo = YOLO(pesos)
    cap = cv2.VideoCapture(indice)
    if not cap.isOpened():
        sys.exit("Não consegui abrir a webcam. Tente outro índice: python webcam_teste.py best.pt 1")

    saida = Path("saida_webcam")
    saida.mkdir(exist_ok=True)
    esticar, salvos, t_ant = True, 0, time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        caixas = detectar(modelo, frame, esticar)
        vis = frame.copy()
        for x1, y1, x2, y2, c in caixas:
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(vis, f"etiqueta {c:.2f}", (x1, max(20, y1 - 8)), FONTE, 0.7, (0, 255, 0), 2)

        agora = time.time()
        fps = 1.0 / max(agora - t_ant, 1e-6)
        t_ant = agora
        modo = "esticado 512x512" if esticar else "normal"
        cv2.putText(vis, f"{fps:.0f} FPS | modo: {modo} | etiquetas: {len(caixas)}",
                    (10, 25), FONTE, 0.6, (0, 255, 255), 2)
        cv2.imshow("IVIS webcam  (q sai | t troca modo | s salva)", vis)

        tecla = cv2.waitKey(1) & 0xFF
        if tecla == ord("q"):
            break
        if tecla == ord("t"):
            esticar = not esticar
        if tecla == ord("s"):
            salvos += 1
            cv2.imwrite(str(saida / f"frame{salvos:03d}.png"), vis)
            if caixas:
                x1, y1, x2, y2, _ = max(caixas, key=lambda b: b[4])
                recorte = frame[max(0, y1):y2, max(0, x1):x2]
                if recorte.size:
                    cv2.imwrite(str(saida / f"recorte{salvos:03d}.png"), recorte)
            print(f"salvo em {saida}/ (#{salvos})")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
