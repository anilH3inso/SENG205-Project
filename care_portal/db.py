from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ------------------------------------------------------------------
# Database URL
#   - You can override with env CARE_PORTAL_DB_URL
# ------------------------------------------------------------------
DATABASE_URL = os.getenv("CARE_PORTAL_DB_URL", "sqlite:///care_portal.db")

# ------------------------------------------------------------------
# Engine & Session
# ------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    echo=False,     # Set True to see SQL logs
    future=True
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
