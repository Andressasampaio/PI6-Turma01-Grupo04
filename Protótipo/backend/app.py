from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename
import json
import unicodedata
from threading import Lock

from yolo_detector import detect_image
from ocr_processor import processar_etiqueta

from database import initialize_database, get_connection

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
