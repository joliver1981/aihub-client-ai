"""Run the Command Center DB migration."""
import config as cfg
import pyodbc

conn = pyodbc.connect(
    f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};"
    f"DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
)
cursor = conn.cursor()

# Read migration file
with open("migrations/004_command_center.sql", "r") as f:
    migration_sql = f.read()

# Split by GO and execute each batch
batches = migration_sql.split("\nGO")
for batch in batches:
    batch = batch.strip()
    if batch and not batch.startswith("--"):
        try:
            cursor.execute(batch)
            conn.commit()
            while cursor.nextset():
                pass
        except Exception as e:
            print(f"Batch error: {e}")

# Verify tables
cursor.execute(
    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_NAME LIKE 'cc_%' ORDER BY TABLE_NAME"
)
tables = [row[0] for row in cursor.fetchall()]
print(f"CC tables: {tables}")
conn.close()
print("Migration complete!")
