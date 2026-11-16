from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename

from yolo_detector import detect_image
from ocr_processor import processar_etiqueta

from database import initialize_database, get_connection

app = Flask(__name__)
CORS(app)

initialize_database()

# Pastas utilizadas pelo processamento de imagens
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
PROCESSED_DIR = BASE_DIR / "processed"

UPLOAD_DIR.mkdir(exist_ok=True)
PROCESSED_DIR.mkdir(exist_ok=True)


# Verifica se o backend está funcionando
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "system": "IVIS",
        "message": "Backend funcionando corretamente"
    }), 200

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
                corrected_lot, corrected_expiry, correction_note
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            inspection_id, data.get("ai_status"), data.get("corrected_status"),
            data.get("corrected_lot"), data.get("corrected_expiry"),
            data.get("correction_note")
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
            correction_note
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        inspection_id,
        inspection["ai_status"],
        data["corrected_status"],
        data.get("corrected_lot"),
        data.get("corrected_expiry"),
        data.get("correction_note")
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
