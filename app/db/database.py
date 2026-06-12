from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Supabase requires SSL — detect if we're connecting to Supabase
# and add the required connection arguments automatically
_is_supabase = "supabase.co" in (DATABASE_URL or "")

if _is_supabase:
    engine = create_engine(
        DATABASE_URL,
        connect_args={
            "sslmode": "require",
            "connect_timeout": 10,
        },
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,       # auto-reconnect if connection drops
        pool_recycle=300,         # recycle connections every 5 minutes
    )
else:
    # Local PostgreSQL (pgAdmin4) — no SSL needed
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()