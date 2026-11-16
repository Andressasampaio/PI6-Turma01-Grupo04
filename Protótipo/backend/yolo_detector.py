from pathlib import Path
from uuid import uuid4

import cv2
from ultralytics import YOLO


# Caminhos do projeto
BASE_DIR = Path(__file__).parent
MODEL_PATH = BASE_DIR / "models" / "best_v3.pt"
OUTPUT_DIR = BASE_DIR / "processed"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# Carrega o modelo uma única vez
if not MODEL_PATH.exists():
    raise FileNotFoundError(
        f"Modelo YOLO não encontrado: {MODEL_PATH}"
    )

model = YOLO(str(MODEL_PATH))


def detect_image(image_path):
    """
    Detecta a etiqueta de medicamento em uma imagem.

    Retorna:
    - status da detecção
    - confiança
    - coordenadas
    - caminho da imagem anotada
    - caminho do recorte da etiqueta
    """

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(
            f"Imagem não encontrada: {image_path}"
        )

    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError("Não foi possível abrir a imagem.")

    results = model.predict(
        source=image,
        conf=0.25,
        verbose=False
    )

    result = results[0]

    if result.boxes is None or len(result.boxes) == 0:
        return {
            "detected": False,
            "message": "Nenhuma etiqueta foi identificada.",
            "confidence": None,
            "coordinates": None,
            "annotated_image": None,
            "crop_image": None
        }

    # Seleciona a detecção com maior confiança
    best_index = int(result.boxes.conf.argmax())

    box = result.boxes.xyxy[best_index].cpu().numpy()
    confidence = float(result.boxes.conf[best_index].cpu().item())

    x1, y1, x2, y2 = map(int, box)

    height, width = image.shape[:2]

    x1 = max(0, min(x1, width))
    x2 = max(0, min(x2, width))
    y1 = max(0, min(y1, height))
    y2 = max(0, min(y2, height))

    if x2 <= x1 or y2 <= y1:
        raise ValueError("A região detectada é inválida.")

    crop = image[y1:y2, x1:x2]

    if crop.size == 0:
        raise ValueError("Não foi possível recortar a etiqueta.")

    # Gera nomes únicos para os arquivos
    unique_id = uuid4().hex

    crop_path = OUTPUT_DIR / f"etiqueta_{unique_id}.jpg"
    annotated_path = OUTPUT_DIR / f"detecao_{unique_id}.jpg"

    # Salva o recorte e a imagem com a detecção
    cv2.imwrite(str(crop_path), crop)
    cv2.imwrite(str(annotated_path), result.plot())

    return {
        "detected": True,
        "class_name": "medicine box",
        "confidence": round(confidence, 4),
        "coordinates": {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2
        },
        "annotated_image": str(annotated_path),
        "crop_image": str(crop_path)
    }
