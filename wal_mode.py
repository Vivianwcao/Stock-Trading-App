import sqlite3
from pathlib import Path
# Run this quick one-time Python script from your project directory to switch your existing database file to WAL mode:

# Ensures it targets the exact db file in your project directory
db_path = Path(__file__).parent / "stocks.db"

conn = sqlite3.connect(db_path)
result = conn.execute("PRAGMA journal_mode = WAL;").fetchone()
print(f"Database journal mode is now: {result[0]}")  # Outputs: wal
conn.close()
