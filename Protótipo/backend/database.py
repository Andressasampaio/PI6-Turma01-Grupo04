import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).parent / "ivis.db"


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

    connection.commit()
    connection.close()
