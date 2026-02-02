from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL
from models import Base, Tenant, TenantAuth

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # Add missing columns if they don't exist (for existing databases)
    try:
        with engine.connect() as conn:
            # Check if last_error column exists
            result = conn.execute(
                text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='tenant_auth' AND column_name='last_error'
                """)
            )
            if result.fetchone() is None:
                conn.execute(text("ALTER TABLE tenant_auth ADD COLUMN last_error TEXT"))
                conn.commit()
            
            # Check if phone_code_hash column exists
            result = conn.execute(
                text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='tenant_auth' AND column_name='phone_code_hash'
                """)
            )
            if result.fetchone() is None:
                conn.execute(text("ALTER TABLE tenant_auth ADD COLUMN phone_code_hash VARCHAR(128)"))
                conn.commit()
            for col, spec in [
                ("code_requested_at", "TIMESTAMP WITH TIME ZONE"),
                ("code_timeout_seconds", "INTEGER"),
            ]:
                r = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='tenant_auth' AND column_name=:c"
                    ),
                    {"c": col},
                )
                if r.fetchone() is None:
                    conn.execute(text(f"ALTER TABLE tenant_auth ADD COLUMN {col} {spec}"))
                    conn.commit()
    except Exception:
        # Column might already exist or table doesn't exist yet - ignore
        pass


def get_session():
    with SessionLocal() as session:
        yield session
