from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename
import json
import unicodedata
from threading import Lock

from yolo_detector import detect_image, detect_video_packages
from ocr_processor import processar_etiqueta, processar_embalagem

from database import initialize_database, get_connection
from gabarito import (
    CAMPOS as CAMPOS_GABARITO, NOMES_CAMPOS, STATUS_DEFINIDO, STATUS_PENDENTE,
    ORIGEM_MANUAL, definir_gabarito, classificar, normalizar_valor,
)

app = Flask(__name__)
CORS(app)

initialize_database()

# Impede duas importações simultâneas (ex.: React StrictMode no desenvolvimento).
IMPORT_TEST_IMAGES_LOCK = Lock()


# Gabarito fixo utilizado no protótipo (Paracetamol 750mg).
GABARITO = {
    "lote": "100648",
    "fabricacao": "JUL/2025",
    "validade": "JUL/2027",
}


# Valor padronizado para campo que o OCR não conseguiu ler (nunca inventar).
NAO_IDENTIFICADO = "Não identificado"


def padronizar_campo(valor):
    texto = str(valor or "").strip()
    if normalizar_leitura(texto) in ("", "NAOIDENTIFICADO", "NAOLEGIVEL", "NONE"):
        return NAO_IDENTIFICADO
    return texto


def normalizar_leitura(valor):
    texto = str(valor or "").upper().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(char for char in texto if unicodedata.category(char) != "Mn")
    return "".join(texto.split())


def normalizar_lote(valor):
    texto = normalizar_leitura(valor)
    if texto.startswith("L") and texto[1:].isdigit():
        texto = texto[1:]
    return texto


def avaliar_conformidade_gabarito(detected, ocr):
    lote = ocr.get("lote")
    fabricacao = ocr.get("fabricacao")
    validade = ocr.get("validade")
    legivel = all(normalizar_leitura(v) not in ("", "NAOIDENTIFICADO", "NAOLEGIVEL")
                  for v in (lote, fabricacao, validade))
    if not detected:
        return "Não Conforme", "Embalagem não localizada pelo YOLO"
    if not legivel:
        return "Não Conforme", "Etiqueta ilegível ou leitura OCR incompleta"

    divergencias = []
    if normalizar_lote(lote) != normalizar_lote(GABARITO["lote"]):
        divergencias.append(f"Lote divergente: {normalizar_lote(lote)} (gabarito {GABARITO['lote']})")
    for campo, rotulo in (("fabricacao", "Fabricação"), ("validade", "Validade")):
        if normalizar_leitura(ocr.get(campo)) != normalizar_leitura(GABARITO[campo]):
            divergencias.append(f"{rotulo} divergente: {ocr.get(campo)} (gabarito {GABARITO[campo]})")
    if divergencias:
        return "Não Conforme", "; ".join(divergencias)
    return "Conforme", "Lote, fabricação e validade correspondem ao gabarito"


def ensure_inspection_columns():
    connection = get_connection()
    inspection_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(inspections)").fetchall()
    }
    for column, column_type in {
        "manufacturing_read": "TEXT",
        "expected_manufacturing": "TEXT",
        "annotated_image": "TEXT",
        "crop_image": "TEXT",
        "ocr_errors": "TEXT",
        "detected": "INTEGER",
    }.items():
        if column not in inspection_columns:
            connection.execute(f"ALTER TABLE inspections ADD COLUMN {column} {column_type}")

    feedback_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(feedbacks)").fetchall()
    }
    if "corrected_manufacturing" not in feedback_columns:
        connection.execute("ALTER TABLE feedbacks ADD COLUMN corrected_manufacturing TEXT")

    connection.commit()
    connection.close()


ensure_inspection_columns()

# Pastas utilizadas pelo processamento de imagens
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
PROCESSED_DIR = BASE_DIR / "processed"
TEST_IMAGES_DIR = BASE_DIR / "test_images"

UPLOAD_DIR.mkdir(exist_ok=True)
PROCESSED_DIR.mkdir(exist_ok=True)
TEST_IMAGES_DIR.mkdir(exist_ok=True)


# Verifica se o backend está funcionando
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "system": "IVIS",
        "message": "Backend funcionando corretamente"
    }), 200



@app.route("/api/import-test-images", methods=["POST"])
def import_test_images():
    """Importa imagens novas uma única vez, sem manter transação aberta durante YOLO/OCR."""
    if not IMPORT_TEST_IMAGES_LOCK.acquire(blocking=False):
        return jsonify({
            "message": "A importação já está em andamento.",
            "imported_count": 0,
            "imported": [],
            "errors": [],
            "busy": True,
        }), 200

    extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    imported = []
    errors = []

    try:
        for image_file in sorted(TEST_IMAGES_DIR.iterdir(), key=lambda item: item.name.lower()):
            if not image_file.is_file() or image_file.suffix.lower() not in extensions:
                continue

            stored_path = f"test_images/{image_file.name}"

            # Consulta e fecha a conexão antes de executar o processamento pesado.
            check_connection = get_connection()
            exists = check_connection.execute(
                "SELECT id FROM inspections WHERE image_path = ?", (stored_path,)
            ).fetchone()
            check_connection.close()
            if exists:
                existing_connection = get_connection()
                try:
                    existing = existing_connection.execute(
                        "SELECT id, lot_read, manufacturing_read, expiry_read, detected, ai_status "
                        "FROM inspections WHERE image_path = ?", (stored_path,)
                    ).fetchone()
                    if existing:
                        cleaned_lot = normalizar_lote(existing["lot_read"])
                        existing_connection.execute(
                            "UPDATE inspections SET lot_read = ?, expected_lot = ? WHERE id = ?",
                            (cleaned_lot or existing["lot_read"], GABARITO["lote"], existing["id"])
                        )
                        existing_connection.commit()
                    if existing and existing["ai_status"] == "Em avaliação":
                        existing_ocr = {
                            "lote": existing["lot_read"],
                            "fabricacao": existing["manufacturing_read"],
                            "validade": existing["expiry_read"],
                        }
                        updated_status, updated_finding = avaliar_conformidade_gabarito(
                            bool(existing["detected"]), existing_ocr
                        )
                        existing_connection.execute("""
                            UPDATE inspections
                            SET expected_lot = ?, expected_expiry = ?, expected_manufacturing = ?,
                                ai_status = ?, ai_finding = ?
                            WHERE id = ?
                        """, (GABARITO["lote"], GABARITO["validade"], GABARITO["fabricacao"],
                              updated_status, updated_finding, existing["id"]))
                        existing_connection.commit()
                finally:
                    existing_connection.close()
                continue

            try:
                detection = detect_image(image_file)
                detected = bool(detection.get("detected"))
                ocr = {}
                annotated_name = (
                    Path(detection["annotated_image"]).name
                    if detection.get("annotated_image") else None
                )
                crop_name = (
                    Path(detection["crop_image"]).name
                    if detection.get("crop_image") else None
                )

                if detected and detection.get("crop_image"):
                    ocr = processar_etiqueta(detection["crop_image"])

                lot = normalizar_lote(ocr.get("lote"))
                if lot:
                    ocr["lote"] = lot
                manufacturing = ocr.get("fabricacao")
                expiry = ocr.get("validade")
                ocr_errors = ocr.get("erros", [])

                ai_status, finding = avaliar_conformidade_gabarito(detected, ocr)

                # Abre a conexão somente para gravar uma inspeção e confirma logo em seguida.
                connection = get_connection()
                try:
                    cursor = connection.execute("""
                        INSERT OR IGNORE INTO inspections (
                            product, lot_read, expected_lot, expiry_read, expected_expiry,
                            ai_status, ai_finding, image_path, operator_status, operator_action,
                            manufacturing_read, expected_manufacturing, annotated_image,
                            crop_image, ocr_errors, detected
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
                    """, (
                        "Paracetamol 750mg", lot, GABARITO["lote"], expiry, GABARITO["validade"],
                        ai_status, finding, stored_path,
                        manufacturing, GABARITO["fabricacao"], annotated_name, crop_name,
                        json.dumps(ocr_errors, ensure_ascii=False), 1 if detected else 0,
                    ))
                    connection.commit()
                    if cursor.rowcount:
                        imported.append({"id": cursor.lastrowid, "filename": image_file.name})
                finally:
                    connection.close()

            except Exception as error:
                errors.append({"filename": image_file.name, "error": str(error)})

        return jsonify({
            "message": "Importação automática concluída",
            "imported_count": len(imported),
            "imported": imported,
            "errors": errors,
        }), 200
    finally:
        IMPORT_TEST_IMAGES_LOCK.release()


@app.route("/api/test-images/<path:filename>", methods=["GET"])
def get_test_image(filename):
    return send_from_directory(TEST_IMAGES_DIR, filename)


# Executa a detecção da etiqueta com o YOLO
@app.route("/api/detect", methods=["POST"])
def detect():
    if "image" not in request.files:
        return jsonify({
            "error": "Nenhuma imagem foi enviada"
        }), 400

    file = request.files["image"]

    if not file.filename:
        return jsonify({
            "error": "Nome do arquivo inválido"
        }), 400

    filename = secure_filename(file.filename)
    upload_path = UPLOAD_DIR / f"{uuid4().hex}_{filename}"

    file.save(upload_path)

    try:
        result = detect_image(upload_path)

        if result["detected"]:
            result["annotated_image_url"] = (
                f"/api/processed/{Path(result['annotated_image']).name}"
            )

            result["crop_image_url"] = (
                f"/api/processed/{Path(result['crop_image']).name}"
            )

            # Executa o OCR sobre o recorte gerado pelo YOLO.
            resultado_ocr = processar_etiqueta(result["crop_image"])
            result["ocr"] = resultado_ocr

        return jsonify(result), 200

    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 500

    finally:
        if upload_path.exists():
            upload_path.unlink()


@app.route("/api/detect-video", methods=["POST"])
def detect_video():
    if "video" not in request.files:
        return jsonify({"error": "Nenhum vídeo foi enviado"}), 400

    file = request.files["video"]

    if not file.filename:
        return jsonify({"error": "Nome do vídeo inválido"}), 400

    allowed_extensions = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    extension = Path(file.filename).suffix.lower()

    if extension not in allowed_extensions:
        return jsonify({"error": "Formato de vídeo não suportado"}), 400

    filename = secure_filename(file.filename)
    upload_path = UPLOAD_DIR / f"{uuid4().hex}_{filename}"

    file.save(upload_path)

    try:
        # O mesmo arquivo enviado de novo geraria inspeções duplicadas.
        # Só reprocessa se o operador pedir explicitamente (reprocessar=1).
        video_hash = _hash_arquivo(upload_path)
        if request.form.get("reprocessar") != "1":
            connection = get_connection()
            existente = connection.execute(
                "SELECT id, created_at FROM processes WHERE video_hash = ? ORDER BY id DESC LIMIT 1",
                (video_hash,)
            ).fetchone()
            connection.close()
            if existente:
                return jsonify({
                    "error": (f"Este vídeo já foi processado (processo #{existente['id']}). "
                              "Envie novamente marcando a opção de reprocessar se quiser repetir."),
                    "duplicate": True,
                    "process_id": existente["id"],
                }), 409

        detected_packages = detect_video_packages(upload_path)

        if not detected_packages:
            return jsonify({
                "error": "Nenhuma embalagem foi identificada no vídeo."
            }), 422

        # Ordem de entrada na cena: define quem são as 3 primeiras embalagens.
        detected_packages = sorted(
            detected_packages,
            key=lambda p: (p.get("first_frame", p.get("frame", 0)), p["track_id"])
        )

        # 1) OCR de cada embalagem, isoladamente.
        leituras = []
        for package in detected_packages:
            crop_path = package["crop_image"]
            # OCR só com recortes desta embalagem (melhor quadro + extras).
            ocr = processar_embalagem(package.get("ocr_crops") or [crop_path])
            ocr["lote"] = normalizar_lote(ocr.get("lote"))
            leituras.append(ocr)

        # 2) Gabarito deste processo, a partir das 3 primeiras embalagens válidas.
        definicao = definir_gabarito([
            {"chave": i, **{c: leituras[i].get(c) for c in CAMPOS_GABARITO}}
            for i in range(len(leituras))
        ])
        referencias = set(definicao["referencias"])

        inspections = []
        connection = get_connection()

        try:
            process_id = connection.execute(
                "INSERT INTO processes (video_name, total_packages, video_hash) VALUES (?, ?, ?)",
                (filename, len(detected_packages), video_hash)
            ).lastrowid
            gabarito = _inserir_gabarito(connection, process_id, definicao)

            for sequence, (package, ocr) in enumerate(
                zip(detected_packages, leituras), start=1
            ):
                crop_path = package["crop_image"]

                # 3) Cada embalagem é comparada só com o gabarito do seu processo.
                ai_status, finding = classificar(ocr, gabarito)

                lot = padronizar_campo(ocr.get("lote"))
                manufacturing = padronizar_campo(ocr.get("fabricacao"))
                expiry = padronizar_campo(ocr.get("validade"))
                # Erros bloqueantes e avisos de qualidade ficam juntos no histórico.
                ocr_errors = ocr.get("erros", []) + ocr.get("avisos", [])

                crop_name = Path(crop_path).name
                annotated_name = Path(
                    package["annotated_image"]
                ).name

                image_path = (
                    f"video:{filename}#track:{package['track_id']}"
                )

                cursor = connection.execute("""
                    INSERT INTO inspections (
                        product,
                        lot_read,
                        expected_lot,
                        expiry_read,
                        expected_expiry,
                        ai_status,
                        ai_finding,
                        image_path,
                        operator_status,
                        operator_action,
                        manufacturing_read,
                        expected_manufacturing,
                        annotated_image,
                        crop_image,
                        ocr_errors,
                        detected,
                        process_id,
                        sequence,
                        track_id,
                        selected_frame,
                        selected_time_s,
                        quality_score,
                        is_reference
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        NULL, NULL, ?, ?, ?, ?, ?, 1,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                """, (
                    "Paracetamol 750mg",
                    lot,
                    _valor_esperado(gabarito, "lote"),
                    expiry,
                    _valor_esperado(gabarito, "validade"),
                    ai_status,
                    finding,
                    image_path,
                    manufacturing,
                    _valor_esperado(gabarito, "fabricacao"),
                    annotated_name,
                    crop_name,
                    json.dumps(ocr_errors, ensure_ascii=False),
                    process_id,
                    sequence,
                    package["track_id"],
                    package.get("frame"),
                    package.get("timestamp_s"),
                    package.get("quality_score"),
                    1 if (sequence - 1) in referencias else 0,
                ))

                inspections.append({
                    "id": cursor.lastrowid,
                    "process_id": process_id,
                    "sequence": sequence,
                    "is_reference": (sequence - 1) in referencias,
                    "track_id": package["track_id"],
                    "lot_read": lot,
                    "manufacturing_read": manufacturing,
                    "expiry_read": expiry,
                    "ai_status": ai_status,
                    "ai_finding": finding,
                    "confidence": package["confidence"],
                    "quality_score": package.get("quality_score"),
                    "frame": package.get("frame"),
                    "timestamp_s": package.get("timestamp_s"),
                    "ocr_readings": ocr.get("leituras", []),
                    "ocr_warnings": ocr.get("avisos", []),
                    "annotated_image": annotated_name,
                    "crop_image": crop_name
                })

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

        return jsonify({
            "message": "Vídeo processado com sucesso",
            "process_id": process_id,
            "gabarito": _gabarito_publico(gabarito, inspections),
            "total_inspecoes": len(inspections),
            "inspections": inspections
        }), 200

    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 500

    finally:
        if upload_path.exists():
            upload_path.unlink()


# ---------------------------------------------------------------------------
# Gabarito por processo (etapa 5)
# ---------------------------------------------------------------------------
VALOR_PENDENTE = "Pendente"


def _hash_arquivo(caminho):
    import hashlib
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _valor_esperado(gabarito, campo):
    return gabarito.get(campo) or VALOR_PENDENTE


def _json(valor):
    return json.dumps(valor, ensure_ascii=False)


def _registrar_evento(connection, gabarito_id, acao, anteriores, novos, observacao=None):
    connection.execute("""
        INSERT INTO gabarito_eventos (gabarito_id, acao, valores_anteriores, valores_novos, observacao)
        VALUES (?, ?, ?, ?, ?)
    """, (gabarito_id, acao, _json(anteriores), _json(novos), observacao))


def _inserir_gabarito(connection, process_id, definicao):
    # As chaves das referências são índices na ordem de entrada; no banco
    # guardamos o número de sequência da embalagem (1, 2, 3...).
    leituras = [
        {**{c: l[c] for c in CAMPOS_GABARITO}, "sequence": l["chave"] + 1}
        for l in definicao["leituras_referencia"]
    ]
    gabarito_id = connection.execute("""
        INSERT INTO gabaritos (process_id, lote, fabricacao, validade, status, origem,
                               motivo, divergencias, leituras_referencia)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        process_id, definicao["lote"], definicao["fabricacao"], definicao["validade"],
        definicao["status"], definicao["origem"], definicao["motivo"],
        _json(definicao["divergencias"]), _json(leituras),
    )).lastrowid
    _registrar_evento(
        connection, gabarito_id, "Definição automática", None,
        {c: definicao[c] for c in CAMPOS_GABARITO} | {"status": definicao["status"]},
        definicao["motivo"],
    )
    return _carregar_gabarito(connection, process_id)


def _carregar_gabarito(connection, process_id):
    row = connection.execute(
        "SELECT * FROM gabaritos WHERE process_id = ?", (process_id,)
    ).fetchone()
    if row is None:
        return None
    gabarito = dict(row)
    gabarito["divergencias"] = json.loads(gabarito["divergencias"] or "[]")
    gabarito["leituras_referencia"] = json.loads(gabarito["leituras_referencia"] or "[]")
    return gabarito


def _gabarito_publico(gabarito, inspections=None):
    """Gabarito com as 3 embalagens de referência ligadas às inspeções."""
    if gabarito is None:
        return None
    publico = dict(gabarito)
    if inspections is not None:
        por_sequencia = {i["sequence"]: i["id"] for i in inspections}
        for leitura in publico["leituras_referencia"]:
            leitura["inspection_id"] = por_sequencia.get(leitura["sequence"])
    publico["pendente"] = gabarito["status"] != STATUS_DEFINIDO
    return publico


def _reclassificar_processo(connection, process_id, gabarito):
    """Reavalia as inspeções do processo ainda não avaliadas pelo operador."""
    rows = connection.execute("""
        SELECT id, lot_read, manufacturing_read, expiry_read FROM inspections
        WHERE process_id = ? AND operator_status IS NULL
    """, (process_id,)).fetchall()
    for row in rows:
        leitura = {
            "lote": row["lot_read"],
            "fabricacao": row["manufacturing_read"],
            "validade": row["expiry_read"],
        }
        status, finding = classificar(leitura, gabarito)
        connection.execute("""
            UPDATE inspections
            SET ai_status = ?, ai_finding = ?, expected_lot = ?,
                expected_manufacturing = ?, expected_expiry = ?
            WHERE id = ?
        """, (status, finding, _valor_esperado(gabarito, "lote"),
              _valor_esperado(gabarito, "fabricacao"),
              _valor_esperado(gabarito, "validade"), row["id"]))
    return len(rows)


@app.route("/api/processes", methods=["GET"])
def list_processes():
    connection = get_connection()
    rows = connection.execute("""
        SELECT p.*, g.status AS gabarito_status, g.lote AS gabarito_lote,
               g.fabricacao AS gabarito_fabricacao, g.validade AS gabarito_validade,
               g.origem AS gabarito_origem
        FROM processes p LEFT JOIN gabaritos g ON g.process_id = p.id
        ORDER BY p.id DESC
    """).fetchall()
    connection.close()
    return jsonify([dict(r) for r in rows]), 200


@app.route("/api/processes/<int:process_id>", methods=["GET"])
def get_process(process_id):
    connection = get_connection()
    process = connection.execute(
        "SELECT * FROM processes WHERE id = ?", (process_id,)
    ).fetchone()
    if process is None:
        connection.close()
        return jsonify({"error": "Processo não encontrado"}), 404
    inspections = [dict(r) for r in connection.execute(
        "SELECT * FROM inspections WHERE process_id = ? ORDER BY sequence", (process_id,)
    ).fetchall()]
    gabarito = _carregar_gabarito(connection, process_id)
    eventos = []
    if gabarito:
        eventos = [dict(r) for r in connection.execute(
            "SELECT * FROM gabarito_eventos WHERE gabarito_id = ? ORDER BY id", (gabarito["id"],)
        ).fetchall()]
    connection.close()
    return jsonify({
        "process": dict(process),
        "gabarito": _gabarito_publico(gabarito, inspections),
        "gabarito_eventos": eventos,
        "inspections": inspections,
    }), 200


@app.route("/api/processes/<int:process_id>/gabarito", methods=["POST"])
def confirm_gabarito(process_id):
    """Confirma (quando pendente) ou corrige o gabarito de UM processo."""
    data = request.get_json(silent=True) or {}
    novos = {c: str(data.get(c) or "").strip() for c in CAMPOS_GABARITO}
    faltantes = [NOMES_CAMPOS[c] for c in CAMPOS_GABARITO if not normalizar_valor(c, novos[c])]
    if faltantes:
        return jsonify({"error": "Informe: " + ", ".join(faltantes)}), 400
    novos["lote"] = normalizar_lote(novos["lote"])
    novos["fabricacao"] = novos["fabricacao"].upper()
    novos["validade"] = novos["validade"].upper()

    connection = get_connection()
    try:
        gabarito = _carregar_gabarito(connection, process_id)
        if gabarito is None:
            return jsonify({"error": "Gabarito do processo não encontrado"}), 404

        anteriores = {c: gabarito[c] for c in CAMPOS_GABARITO} | {"status": gabarito["status"]}
        acao = ("Confirmação manual" if gabarito["status"] == STATUS_PENDENTE
                else "Correção manual")
        nota = (data.get("observacao") or "").strip() or None

        connection.execute("""
            UPDATE gabaritos
            SET lote = ?, fabricacao = ?, validade = ?, status = ?, origem = ?,
                motivo = ?, confirmed_at = CURRENT_TIMESTAMP, confirmation_note = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (novos["lote"], novos["fabricacao"], novos["validade"], STATUS_DEFINIDO,
              ORIGEM_MANUAL, f"{acao} pelo operador.", nota, gabarito["id"]))
        _registrar_evento(connection, gabarito["id"], acao, anteriores,
                          novos | {"status": STATUS_DEFINIDO}, nota)

        gabarito = _carregar_gabarito(connection, process_id)
        reavaliadas = _reclassificar_processo(connection, process_id, gabarito)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return jsonify({
        "message": f"{acao} registrada",
        "gabarito": _gabarito_publico(gabarito),
        "inspecoes_reavaliadas": reavaliadas,
    }), 200


# Disponibiliza as imagens processadas pela API
@app.route("/api/processed/<path:filename>", methods=["GET"])
def get_processed_image(filename):
    return send_from_directory(PROCESSED_DIR, filename)

# Consulta todas as inspeções
@app.route("/api/inspections", methods=["GET"])
def get_inspections():
    connection = get_connection()

    inspections = connection.execute("""
        SELECT * FROM inspections
        ORDER BY id DESC
    """).fetchall()

    connection.close()

    return jsonify([dict(item) for item in inspections]), 200


# Registra uma nova inspeção
@app.route("/api/inspections", methods=["POST"])
def create_inspection():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Dados não enviados"}), 400

    if not data.get("product") or not data.get("ai_status"):
        return jsonify({
            "error": "Produto e classificação da IA são obrigatórios"
        }), 400

    connection = get_connection()

    cursor = connection.execute("""
        INSERT INTO inspections (
            product, lot_read, expected_lot, expiry_read, expected_expiry,
            ai_status, ai_finding, image_path, operator_status,
            operator_action, reviewed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END)
    """, (
        data.get("product"), data.get("lot_read"), data.get("expected_lot"),
        data.get("expiry_read"), data.get("expected_expiry"),
        data.get("ai_status"), data.get("ai_finding"), data.get("image_path"),
        data.get("operator_status"), data.get("operator_action"),
        data.get("operator_status")
    ))

    inspection_id = cursor.lastrowid
    feedback_id = None

    # Quando houver correção, registra inspeção e feedback na mesma transação.
    if data.get("corrected_status"):
        feedback_cursor = connection.execute("""
            INSERT INTO feedbacks (
                inspection_id, original_status, corrected_status,
                corrected_lot, corrected_expiry, correction_note,
                corrected_manufacturing
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            inspection_id, data.get("ai_status"), data.get("corrected_status"),
            data.get("corrected_lot"), data.get("corrected_expiry"),
            data.get("correction_note"), data.get("corrected_manufacturing")
        ))
        feedback_id = feedback_cursor.lastrowid
        connection.execute("""
            UPDATE inspections
            SET operator_status = ?, operator_action = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (data.get("corrected_status"), "Corrigido pelo operador", inspection_id))

    connection.commit()
    connection.close()

    return jsonify({
        "message": "Inspeção registrada com sucesso",
        "id": inspection_id,
        "feedback_id": feedback_id
    }), 201


# Consulta uma inspeção específica
@app.route("/api/inspections/<int:inspection_id>", methods=["GET"])
def get_inspection(inspection_id):
    connection = get_connection()

    inspection = connection.execute("""
        SELECT * FROM inspections WHERE id = ?
    """, (inspection_id,)).fetchone()

    connection.close()

    if inspection is None:
        return jsonify({"error": "Inspeção não encontrada"}), 404

    return jsonify(dict(inspection)), 200


# Confirma uma inspeção já existente sem criar uma linha duplicada.
@app.route("/api/inspections/<int:inspection_id>/confirm", methods=["POST"])
def confirm_inspection(inspection_id):
    data = request.get_json(silent=True) or {}
    connection = get_connection()

    inspection = connection.execute(
        "SELECT id, ai_status, operator_status FROM inspections WHERE id = ?",
        (inspection_id,)
    ).fetchone()

    if inspection is None:
        connection.close()
        return jsonify({"error": "Inspeção não encontrada"}), 404

    if inspection["operator_status"]:
        connection.close()
        return jsonify({"error": "Esta inspeção já foi avaliada"}), 409

    operator_status = data.get("operator_status") or inspection["ai_status"]
    if operator_status not in ("Conforme", "Não Conforme"):
        connection.close()
        return jsonify({"error": "Veredito do operador inválido"}), 400

    connection.execute("""
        UPDATE inspections
        SET operator_status = ?,
            operator_action = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (operator_status, "Resultado confirmado pelo operador", inspection_id))

    connection.commit()
    connection.close()

    return jsonify({
        "message": "Inspeção confirmada com sucesso",
        "inspection_id": inspection_id,
        "operator_status": operator_status
    }), 200


# Registra a correção realizada pelo operador
@app.route(
    "/api/inspections/<int:inspection_id>/feedback",
    methods=["POST"]
)
def create_feedback(inspection_id):
    data = request.get_json(silent=True)

    if not data or not data.get("corrected_status"):
        return jsonify({
            "error": "A classificação corrigida é obrigatória"
        }), 400

    connection = get_connection()

    inspection = connection.execute("""
        SELECT * FROM inspections WHERE id = ?
    """, (inspection_id,)).fetchone()

    if inspection is None:
        connection.close()
        return jsonify({
            "error": "Inspeção não encontrada"
        }), 404

    cursor = connection.execute("""
        INSERT INTO feedbacks (
            inspection_id,
            original_status,
            corrected_status,
            corrected_lot,
            corrected_expiry,
            correction_note,
            corrected_manufacturing
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        inspection_id,
        inspection["ai_status"],
        data["corrected_status"],
        data.get("corrected_lot"),
        data.get("corrected_expiry"),
        data.get("correction_note"),
        data.get("corrected_manufacturing")
    ))

    connection.execute("""
        UPDATE inspections
        SET operator_status = ?,
            operator_action = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        data["corrected_status"],
        "Corrigido pelo operador",
        inspection_id
    ))

    connection.commit()
    feedback_id = cursor.lastrowid
    connection.close()

    return jsonify({
        "message": "Correção registrada com sucesso",
        "feedback_id": feedback_id,
        "inspection_id": inspection_id
    }), 201


# Consulta todos os feedbacks
@app.route("/api/feedbacks", methods=["GET"])
def get_feedbacks():
    connection = get_connection()

    feedbacks = connection.execute("""
        SELECT
            feedbacks.*,
            inspections.product,
            inspections.ai_finding
        FROM feedbacks
        INNER JOIN inspections
            ON inspections.id = feedbacks.inspection_id
        ORDER BY feedbacks.id DESC
    """).fetchall()

    connection.close()

    return jsonify([dict(item) for item in feedbacks]), 200


# Indicadores da Visão Geral
@app.route("/api/stats", methods=["GET"])
def get_stats():
    connection = get_connection()

    total = connection.execute("""
        SELECT COUNT(*) FROM inspections
    """).fetchone()[0]

    conformes = connection.execute("""
        SELECT COUNT(*) FROM inspections
        WHERE COALESCE(operator_status, ai_status) = 'Conforme'
    """).fetchone()[0]

    nao_conformes = connection.execute("""
        SELECT COUNT(*) FROM inspections
        WHERE COALESCE(operator_status, ai_status) = 'Não Conforme'
    """).fetchone()[0]

    pendentes = connection.execute("""
        SELECT COUNT(*) FROM inspections
        WHERE operator_status IS NULL
    """).fetchone()[0]

    connection.close()

    return jsonify({
        "total": total,
        "conformes": conformes,
        "nao_conformes": nao_conformes,
        "pendentes": pendentes
    }), 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
