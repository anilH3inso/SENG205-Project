from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ------------------------------------------------------------------
# Database URL
# ------------------------------------------------------------------
# For SQLite (local file):
#     "sqlite:///care_portal.db"
#
# For SQLite (in-memory, for testing):
#     "sqlite:///:memory:"
#
# For MySQL (with PyMySQL driver):
#     "mysql+pymysql://user:password@localhost:3306/care_portal"
#
# For PostgreSQL (with psycopg driver):
#     "postgresql+psycopg://user:password@localhost:5432/care_portal"
# ------------------------------------------------------------------

DATABASE_URL = "sqlite:///care_portal.db"

# ------------------------------------------------------------------
# Engine & Session
# ------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    echo=False,         # Set to True for SQL debug logs
    future=True         # SQLAlchemy 2.0 style
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # prevents detached instance errors
    future=True
)

# ------------------------------------------------------------------
# Declarative Base
# ------------------------------------------------------------------
Base = declarative_base()
