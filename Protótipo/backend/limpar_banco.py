
from pathlib import Path

from database import get_connection

BASE_DIR = Path(__file__).resolve().parent

PASTAS_LIMPAR = [
    BASE_DIR / "processed",
    BASE_DIR / "uploads",
]

TABELAS = [
    "gabarito_eventos",
    "gabaritos",
    "feedbacks",
    "inspections",
    "processes",
]


def limpar_arquivos(pasta):
    if not pasta.exists():
        return 0

    removidos = 0

    for item in pasta.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()
            removidos += 1

        elif item.is_dir():
            for filho in sorted(
                item.rglob("*"),
                key=lambda p: len(p.parts),
                reverse=True
            ):
                if filho.is_file() or filho.is_symlink():
                    filho.unlink()
                    removidos += 1
                elif filho.is_dir():
                    filho.rmdir()

            item.rmdir()

    return removidos


def limpar_banco():
    connection = get_connection()
    totais = {}

    try:
        existentes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        connection.execute("BEGIN")

        # Exclui primeiro as tabelas dependentes por causa das FKs.
        for tabela in TABELAS:
            if tabela in existentes:
                totais[tabela] = connection.execute(
                    f"SELECT COUNT(*) FROM {tabela}"
                ).fetchone()[0]

                connection.execute(f"DELETE FROM {tabela}")

        # Reinicia os IDs.
        if "sqlite_sequence" in existentes:
            nomes = tuple(
                tabela for tabela in TABELAS
                if tabela in existentes
            )

            if nomes:
                placeholders = ",".join("?" for _ in nomes)

                connection.execute(
                    f"""
                    DELETE FROM sqlite_sequence
                    WHERE name IN ({placeholders})
                    """,
                    nomes
                )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    # Remove imagens processadas e arquivos temporários.
    arquivos_removidos = {}

    for pasta in PASTAS_LIMPAR:
        arquivos_removidos[pasta.name] = limpar_arquivos(pasta)

    print("\n========== IVIS LIMPO ==========\n")

    print("REGISTROS REMOVIDOS:")

    for tabela in TABELAS:
        print(f"{tabela}: {totais.get(tabela, 0)}")

    print("\nARQUIVOS REMOVIDOS:")

    for pasta, quantidade in arquivos_removidos.items():
        print(f"{pasta}/: {quantidade}")

    print("\nBanco de dados preservado.")
    print("Modelo YOLO preservado.")
    print("Imagens de teste preservadas.")
    print("\nLimpeza concluída!")


if __name__ == "__main__":
    try:
        limpar_banco()
    except Exception as erro:
        print(f"\nErro ao limpar o banco: {erro}")
        raise
