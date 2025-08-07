import os
from sqlalchemy import create_engine, MetaData
from dotenv import load_dotenv

load_dotenv()

def get_engine():
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise ValueError("DB_URL not set in .env")
    return create_engine(db_url)

def reflect_schema():
    engine = get_engine()
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return metadata,engine
