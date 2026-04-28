from app.db import SessionLocal, engine
from modules.communication_crm import create_crm_tables
from modules.communication_crm.crm_service import seed_demo


def run() -> None:
    create_crm_tables(engine)
    db = SessionLocal()
    try:
        result = seed_demo(db)
        created = result.get("created", 0)
        print(f"CRM demo seed complete: {created} records created.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
