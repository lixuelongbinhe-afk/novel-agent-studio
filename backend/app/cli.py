from app.database import SessionLocal
from app.migrations import upgrade_database
from app.repositories import create_seed_data


def migrate() -> None:
    upgrade_database()
    print("Database schema is ready.")


def seed() -> None:
    upgrade_database()
    with SessionLocal() as db:
        with db.begin():
            project = create_seed_data(db)
            print(f"Seeded project: {project.title}")


if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "migrate"
    if command == "migrate":
        migrate()
    elif command == "seed":
        seed()
    else:
        raise SystemExit(f"Unknown command: {command}")
