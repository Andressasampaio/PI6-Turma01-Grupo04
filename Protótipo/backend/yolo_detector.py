
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from ultralytics import YOLO


BASE_DIR = Path(__file__).parent
MODEL_PATH = BASE_DIR / "models" / "best_v3.pt"
OUTPUT_DIR = BASE_DIR / "processed"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

if not MODEL_PATH.exists():
    raise FileNotFoundError(f"Modelo YOLO não encontrado: {MODEL_PATH}")

model = YOLO(str(MODEL_PATH))

# Parâmetros de inferência (validados com tools/diagnostico_yolo.py em 2026-11-25).
# - Embalagens reais inteiras: confiança 0,75–0,92 e área de 3–18% do quadro.
# - Falsos positivos em quadros vazios (fundo): caixas de 57–89% da área,
#   sempre com confiança <= 0,48.
INFER_IMGSZ = 512          # mesma resolução do treino (train_args.imgsz)
TRACK_CONF = 0.10          # baixa: o ByteTrack usa caixas fracas para associar
CANDIDATE_MIN_CONF = 0.50  # mínima para um quadro virar candidato a melhor captura
MAX_AREA_RATIO = 0.40      # caixa acima disso é tratada como fundo

# Rastreamento (etapa 2). O ByteTrack às vezes troca o ID de uma mesma
# embalagem após 1–2 quadros sem detecção (ex.: vídeo 22.39.13, #20 -> #21).
# Trechos que não coexistem, separados por pouco tempo e com posição contínua
# são unidos em uma única embalagem.
MERGE_MAX_GAP_S = 0.6      # maior intervalo sem detecção entre dois trechos
MERGE_MAX_DIST = 0.6       # distância máxima, em larguras de caixa
MIN_TRACK_HITS = 3         # aparições mínimas para gerar uma inspeção

# Seleção do melhor quadro (etapa 3). Medições em 2026-11-25 mostraram que a
# nitidez da caixa inteira passa de 250 em todos os quadros (nota sempre 1,0)
# e que caixas parciais (só parte da embalagem) parecem "mais nítidas".
# Por isso a nota final é relativa ao histórico de cada embalagem.
EDGE_MARGIN_RATIO = 0.03   # caixa a menos de 3% da borda = embalagem possivelmente cortada
SHARPNESS_WIDTH = 320    # largura usada para medir nitidez sem efeito de escala
MIN_COMPLETENESS = 0.90    # tamanho mínimo em relação ao tamanho típico da embalagem
PESOS_QUADRO = {
    "completude": 0.30,    # caixa cobre a embalagem inteira
    "nitidez": 0.35,       # nitidez da etiqueta (relativa ao melhor quadro da trilha)
    "centro": 0.20,        # centralização no quadro
    "confianca": 0.15,     # confiança do YOLO
}


CROP_MARGIN_RATIO = 0.12   # margem do recorte, em fração da largura/altura da caixa
OCR_EXTRA_FRAMES = 2       # quadros extras por embalagem para votação do OCR (total 3)


def _recorte_com_margem(frame, x1, y1, x2, y2):
    h, w = frame.shape[:2]
    mx = int((x2 - x1) * CROP_MARGIN_RATIO)
    my = int((y2 - y1) * CROP_MARGIN_RATIO)
    return frame[max(0, y1 - my):min(h, y2 + my), max(0, x1 - mx):min(w, x2 + mx)].copy()


def _percentil(valores, p):
    ordenados = sorted(valores)
    if not ordenados:
        return 0.0
    k = min(len(ordenados) - 1, max(0, round(p * (len(ordenados) - 1))))
    return float(ordenados[k])


def _escolher_melhor_quadro(candidatos):
    """Pontua os candidatos de UMA embalagem e devolve (melhor, pontuação)."""
    if not candidatos:
        return None, -1.0

    ref_w = _percentil([c["width"] for c in candidatos], 0.8) or 1.0
    ref_h = _percentil([c["height"] for c in candidatos], 0.8) or 1.0
    max_nit = max(c["label_sharpness"] for c in candidatos) or 1.0

    for c in candidatos:
        c["completeness"] = min(c["width"] / ref_w, 1.0) * min(c["height"] / ref_h, 1.0)
        nota_nit = c["label_sharpness"] / max_nit
        c["final_score"] = (
            PESOS_QUADRO["completude"] * c["completeness"]
            + PESOS_QUADRO["nitidez"] * nota_nit
            + PESOS_QUADRO["centro"] * c["center_score"]
            + PESOS_QUADRO["confianca"] * c["confidence"]
        )

    # Quadros em que a caixa cobre só parte da embalagem ficam fora,
    # desde que exista ao menos um quadro completo.
    completos = [c for c in candidatos if c["completeness"] >= MIN_COMPLETENESS]
    melhor = max(completos or candidatos, key=lambda c: c["final_score"])
    return melhor, melhor["final_score"]


def _desenhar_anotacao(frame_jpg, coordenadas, rotulo):
    """Quadro anotado só com a caixa desta embalagem."""
    frame = cv2.imdecode(frame_jpg, cv2.IMREAD_COLOR)
    p1 = (coordenadas["x1"], coordenadas["y1"])
    p2 = (coordenadas["x2"], coordenadas["y2"])
    cv2.rectangle(frame, p1, p2, (255, 120, 0), 2)
    cv2.putText(frame, rotulo, (p1[0], max(14, p1[1] - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 120, 0), 2)
    return frame


def _centro(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def _unir_trechos(tracks, max_gap_frames):
    """Une IDs do ByteTrack que pertencem à mesma embalagem.

    B é continuação de A quando: B começa depois do fim de A (não coexistem),
    o intervalo é <= max_gap_frames e o início de B está próximo da posição
    prevista de A (último centro + velocidade). Entre vários A possíveis,
    escolhe o mais próximo. Retorna {id_raiz: track_unido} com "ids".
    """
    ordem = sorted(tracks, key=lambda t: tracks[t]["observacoes"][0][0])
    raiz = {t: t for t in ordem}
    tem_sucessor = set()

    for b in ordem:
        b_ini_frame, b_ini_box = tracks[b]["observacoes"][0]
        bx, by = _centro(b_ini_box)
        melhor, melhor_dist = None, None

        for a in ordem:
            if a == b or a in tem_sucessor:
                continue
            obs_a = tracks[a]["observacoes"]
            a_fim_frame, a_fim_box = obs_a[-1]
            gap = b_ini_frame - a_fim_frame
            if gap <= 0 or gap > max_gap_frames:
                continue

            ax, ay = _centro(a_fim_box)
            if len(obs_a) >= 2:
                f0, box0 = obs_a[-2]
                px, py = _centro(box0)
                passos = max(1, a_fim_frame - f0)
                ax += (ax - px) / passos * gap
                ay += (ay - py) / passos * gap

            largura = max(a_fim_box[2] - a_fim_box[0], b_ini_box[2] - b_ini_box[0], 1)
            dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 / largura
            if dist <= MERGE_MAX_DIST and (melhor_dist is None or dist < melhor_dist):
                melhor, melhor_dist = a, dist

        if melhor is not None:
            tem_sucessor.add(melhor)
            raiz[b] = raiz[melhor]

    unidos = {}
    for t in ordem:
        r = raiz[t]
        trk = tracks[t]
        if r not in unidos:
            unidos[r] = {**trk, "ids": [t], "observacoes": list(trk["observacoes"])}
            continue
        u = unidos[r]
        u["ids"].append(t)
        u["hits"] += trk["hits"]
        u["observacoes"].extend(trk["observacoes"])
        u["candidatos"] = u["candidatos"] + trk["candidatos"]
    return unidos


def _salvar_imagem(caminho, imagem):
    # imencode + tofile funciona com acentos no caminho (ex.: "IVIS-Protótipo"),
    # o que cv2.imwrite não garante no Windows.
    caminho = Path(caminho)
    ok, buffer = cv2.imencode(caminho.suffix or ".jpg", imagem)
    if not ok:
        raise ValueError(f"Não foi possível salvar a imagem: {caminho}")
    buffer.tofile(str(caminho))


def ler_imagem(caminho, flags=cv2.IMREAD_COLOR):
    """Lê imagem aceitando acentos no caminho; devolve None se não abrir."""
    try:
        dados = np.fromfile(str(caminho), dtype=np.uint8)
    except OSError:
        return None
    if dados.size == 0:
        return None
    return cv2.imdecode(dados, flags)


def detect_image(image_path):
    """Mantém a detecção de imagem individual para compatibilidade."""
    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {image_path}")

    image = ler_imagem(image_path)

    if image is None:
        raise ValueError("Não foi possível abrir a imagem.")

    results = model.predict(source=image, conf=0.25, verbose=False)
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

    unique_id = uuid4().hex
    crop_path = OUTPUT_DIR / f"etiqueta_{unique_id}.jpg"
    annotated_path = OUTPUT_DIR / f"detecao_{unique_id}.jpg"

    _salvar_imagem(crop_path, crop)
    _salvar_imagem(annotated_path, result.plot())

    return {
        "detected": True,
        "class_name": "medicine box",
        "confidence": round(confidence, 4),
        "coordinates": {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2
        },
        "annotated_image": str(annotated_path),
        "crop_image": str(crop_path)
    }



def _avaliar_quadro(frame, box, confidence):
    """Avalia a qualidade de um quadro para uma embalagem."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = map(int, box)

    # O YOLO costuma parar a caixa alguns pixels antes da borda quando a
    # embalagem já está saindo do quadro (visto no vídeo 22.39.13, quadro 918).
    margem = max(2, int(min(width, height) * EDGE_MARGIN_RATIO))
    # Limitação conhecida (etapa 10, cenários 4 e 9): quando a caixa do YOLO
    # cobre só a face da etiqueta, uma embalagem com a ponta fora do quadro
    # ainda passa nesta checagem. Uma margem proporcional à caixa (20%) foi
    # testada e descartada: não resolveu e eliminou embalagens legítimas.

    totalmente_visivel = (
        x1 > margem and y1 > margem
        and x2 < width - margem and y2 < height - margem
    )
    # Não aceita embalagens cortadas pelas bordas.
    if not totalmente_visivel:
        return None

    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))

    if x2 - x1 < 2 or y2 - y1 < 2:
        return None

    # Descarta detecções que ocupam uma área exagerada da imagem.
    area_caixa = (x2 - x1) * (y2 - y1)
    area_imagem = width * height

    if area_caixa / area_imagem > MAX_AREA_RATIO:
        return None

    # Confiança baixa só aparece em fundo vazio ou embalagem entrando/saindo.
    if float(confidence) < CANDIDATE_MIN_CONF:
        return None

    crop = frame[y1:y2, x1:x2]

    if crop.size == 0:
        return None

    # 1. Nitidez
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    nitidez = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    nota_nitidez = min(nitidez / 250.0, 1.0)

    # Nitidez independente do tamanho: o recorte é levado a uma largura fixa
    # e só a região interna (onde fica a etiqueta) é medida. Usada na seleção
    # final, comparada com os outros quadros da mesma embalagem.
    escala = SHARPNESS_WIDTH / float(gray.shape[1])
    normal = cv2.resize(gray, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    nh, nw = normal.shape[:2]
    interna = normal[int(nh * 0.10):int(nh * 0.90), int(nw * 0.10):int(nw * 0.90)]
    nitidez_etiqueta = float(cv2.Laplacian(interna, cv2.CV_64F).var()) if interna.size else 0.0

    # 2. Centralização
    centro_x = (x1 + x2) / 2
    centro_y = (y1 + y2) / 2

    dx = abs(centro_x - width / 2) / max(width / 2, 1)
    dy = abs(centro_y - height / 2) / max(height / 2, 1)

    nota_centro = max(
        0.0,
        1.0 - ((dx * dx + dy * dy) / 2) ** 0.5
    )

    # 3. Tamanho da embalagem na imagem
    proporcao = (
        (x2 - x1) * (y2 - y1)
    ) / float(width * height)

    nota_tamanho = min(proporcao / 0.12, 1.0)

    # 4. Confiança do YOLO
    nota_confianca = max(0.0, min(float(confidence), 1.0))

    # 5. Embalagem totalmente visível
    nota_visibilidade = 1.0 if totalmente_visivel else 0.0

    # Pontuação final
    pontuacao = (
        0.40 * nota_nitidez
        + 0.25 * nota_centro
        + 0.15 * nota_confianca
        + 0.10 * nota_tamanho
        + 0.10 * nota_visibilidade
    )

    return {
        # Recorte enviado ao OCR: embalagem inteira + margem, porque a caixa do
        # YOLO às vezes fica alguns pixels menor que a embalagem (etapa 3, id 14).
        "crop": _recorte_com_margem(frame, x1, y1, x2, y2),
        "score": pontuacao,
        "sharpness": nitidez,
        "label_sharpness": nitidez_etiqueta,
        "width": x2 - x1,
        "height": y2 - y1,
        "confidence": nota_confianca,
        "center_score": nota_centro,
        "fully_visible": totalmente_visivel,
        "coordinates": {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2
        }
    }


def detect_video_packages(video_path, sample_fps=5):
    """
    Seleciona o melhor quadro de cada embalagem individualmente.
    Cada track_id possui sua própria captura e pontuação.
    """

    video_path = Path(video_path)

    if not video_path.exists():
        raise FileNotFoundError(
            f"Vídeo não encontrado: {video_path}"
        )

    if sample_fps <= 0:
        raise ValueError("sample_fps precisa ser maior que zero.")

    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        raise ValueError("Não foi possível abrir o vídeo.")

    fps = capture.get(cv2.CAP_PROP_FPS) or 25
    frame_step = max(1, round(fps / sample_fps))

    tracks = {}
    frame_number = 0
    primeiro_quadro = True

    try:
        while True:
            success, frame = capture.read()

            if not success:
                break

            current_frame = frame_number
            frame_number += 1

            if current_frame % frame_step != 0:
                continue

            # persist=False no primeiro quadro recria o rastreador: cada vídeo
            # começa sem IDs/estado do vídeo anterior.
            results = model.track(
                source=frame,
                persist=not primeiro_quadro,
                tracker="bytetrack.yaml",
                conf=TRACK_CONF,
                imgsz=INFER_IMGSZ,
                verbose=False
            )
            primeiro_quadro = False

            if not results:
                continue

            result = results[0]
            boxes = result.boxes

            if boxes is None or len(boxes) == 0:
                continue

            if boxes.id is None:
                continue

            track_ids = boxes.id.int().cpu().tolist()
            coordinates = boxes.xyxy.cpu().numpy()
            confidences = boxes.conf.cpu().numpy()

            # Quadro original comprimido; a anotação é feita depois, só com a
            # caixa da embalagem escolhida (sem caixas de outras embalagens).
            frame_jpg = None

            for track_id, box, confidence in zip(
                track_ids, coordinates, confidences
            ):
                track_id = int(track_id)

                # Cada embalagem recebe seu próprio histórico, inclusive dos
                # quadros em que ainda está parcial (usado para unir trechos).
                if track_id not in tracks:
                    tracks[track_id] = {
                        "hits": 0,
                        "observacoes": [],
                        "candidatos": []
                    }

                track = tracks[track_id]
                track["hits"] += 1
                track["observacoes"].append(
                    (current_frame, tuple(float(v) for v in box))
                )

                candidato = _avaliar_quadro(
                    frame, box, confidence
                )

                if candidato is None:
                    continue

                # Todos os quadros válidos são guardados; a escolha é feita
                # no fim, comparando a embalagem com ela mesma.
                if frame_jpg is None:
                    frame_jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])[1]
                candidato["frame"] = current_frame
                candidato["frame_jpg"] = frame_jpg
                track["candidatos"].append(candidato)

    finally:
        capture.release()

    embalagens = _unir_trechos(tracks, max_gap_frames=round(fps * MERGE_MAX_GAP_S))

    inspections = []

    # Salva uma imagem independente para cada embalagem.
    for track_id, track in embalagens.items():

        best, score = _escolher_melhor_quadro(track["candidatos"])

        # Exige a embalagem inteira em pelo menos um quadro e um mínimo de
        # aparições, para não gerar inspeção de ruído ou de embalagem parcial.
        if best is None or track["hits"] < MIN_TRACK_HITS:
            continue

        unique_id = uuid4().hex

        crop_path = OUTPUT_DIR / f"video_crop_{unique_id}.jpg"
        annotated_path = OUTPUT_DIR / f"video_frame_{unique_id}.jpg"

        _salvar_imagem(crop_path, best["crop"])

        # Quadros extras da MESMA embalagem para votação do OCR (etapa 4).
        ocr_crops = [str(crop_path)]
        completos = [c for c in track["candidatos"]
                     if c is not best and c["completeness"] >= MIN_COMPLETENESS]
        for extra in sorted(completos, key=lambda c: c["final_score"], reverse=True)[:OCR_EXTRA_FRAMES]:
            extra_path = OUTPUT_DIR / f"video_ocr_{unique_id}_{extra['frame']}.jpg"
            _salvar_imagem(extra_path, extra["crop"])
            ocr_crops.append(str(extra_path))

        _salvar_imagem(annotated_path, _desenhar_anotacao(
            best["frame_jpg"], best["coordinates"],
            f"embalagem {track_id} ({best['confidence']:.2f})"
        ))

        inspections.append({
            "track_id": track_id,
            "confidence": round(best["confidence"], 4),
            "quality_score": round(score, 4),
            "sharpness": round(best["sharpness"], 2),
            "label_sharpness": round(best["label_sharpness"], 2),
            "completeness": round(best["completeness"], 4),
            "center_score": round(best["center_score"], 4),
            "fully_visible": best["fully_visible"],
            "frame": best["frame"],
            "timestamp_s": round(best["frame"] / fps, 2),
            "merged_track_ids": track["ids"],
            "first_frame": track["observacoes"][0][0],
            "last_frame": track["observacoes"][-1][0],
            "coordinates": best["coordinates"],
            "crop_image": str(crop_path),
            "ocr_crops": ocr_crops,
            "annotated_image": str(annotated_path)
        })

    return inspections
