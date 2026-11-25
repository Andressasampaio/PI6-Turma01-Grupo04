import os
import sqlite3
from pathlib import Path

# IVIS_DB_PATH permite testar com uma cópia do banco sem tocar no ivis.db real.
DATABASE_PATH = Path(os.environ.get("IVIS_DB_PATH") or Path(__file__).parent / "ivis.db")


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database():
    connection = get_connection()
    connection.execute("PRAGMA journal_mode = WAL")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL,
            lot_read TEXT,
            expected_lot TEXT,
            expiry_read TEXT,
            expected_expiry TEXT,
            ai_status TEXT NOT NULL,
            ai_finding TEXT,
            image_path TEXT,
            operator_status TEXT,
            operator_action TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id INTEGER NOT NULL,
            original_status TEXT NOT NULL,
            corrected_status TEXT NOT NULL,
            corrected_lot TEXT,
            corrected_expiry TEXT,
            correction_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (inspection_id)
                REFERENCES inspections(id)
        )
    """)

    feedback_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(feedbacks)").fetchall()
    }
    if "correction_note" not in feedback_columns:
        connection.execute("ALTER TABLE feedbacks ADD COLUMN correction_note TEXT")

    # --- Etapa 5: processo de inspeção (um por vídeo) e seu gabarito ---
    connection.execute("""
        CREATE TABLE IF NOT EXISTS processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_name TEXT NOT NULL,
            total_packages INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Um gabarito por processo (UNIQUE): não é compartilhado entre vídeos.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gabaritos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process_id INTEGER NOT NULL UNIQUE,
            lote TEXT,
            fabricacao TEXT,
            validade TEXT,
            status TEXT NOT NULL,
            origem TEXT NOT NULL,
            motivo TEXT,
            divergencias TEXT,
            leituras_referencia TEXT,
            confirmed_at TEXT,
            confirmation_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (process_id) REFERENCES processes(id)
        )
    """)

    # Registro de toda definição, confirmação ou correção do gabarito.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gabarito_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gabarito_id INTEGER NOT NULL,
            acao TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_novos TEXT,
            observacao TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gabarito_id) REFERENCES gabaritos(id)
        )
    """)

    process_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(processes)").fetchall()
    }
    if "video_hash" not in process_columns:
        # SHA-256 do arquivo: identifica o reenvio do mesmo vídeo.
        connection.execute("ALTER TABLE processes ADD COLUMN video_hash TEXT")

    inspection_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(inspections)").fetchall()
    }
    for column, column_type in {
        "process_id": "INTEGER REFERENCES processes(id)",
        "sequence": "INTEGER",          # ordem de entrada da embalagem no vídeo
        "track_id": "INTEGER",
        "selected_frame": "INTEGER",
        "selected_time_s": "REAL",
        "quality_score": "REAL",
        "is_reference": "INTEGER DEFAULT 0",  # 1 = uma das 3 que definiram o gabarito
    }.items():
        if column not in inspection_columns:
            connection.execute(f"ALTER TABLE inspections ADD COLUMN {column} {column_type}")

    connection.commit()
    connection.close()
