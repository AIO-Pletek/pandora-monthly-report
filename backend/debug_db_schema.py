"""Check Pandora DB schema — find correct agent display name column."""
import pymysql

conn = pymysql.connect(
    host="0.0.0.0", user="devops", password="REDACTED",
    database="pandora", charset="utf8", connect_timeout=10,
)

cur = conn.cursor()

# 1. List all columns in tagente
print("=== tagente columns ===")
cur.execute("DESCRIBE tagente")
for row in cur.fetchall():
    print(f"  {row[0]:30s} {row[1]:20s}")

# 2. Show first 3 agent rows (all columns)
print("\n=== tagente sample (first 3 rows) ===")
cur.execute("SELECT * FROM tagente WHERE disabled = 0 LIMIT 3")
columns = [d[0] for d in cur.description]
print(f"Columns: {columns}")
for row in cur.fetchall():
    print(dict(zip(columns, row)))

# 3. Show tagente_modulo columns
print("\n=== tagente_modulo columns ===")
cur.execute("DESCRIBE tagente_modulo")
for row in cur.fetchall():
    print(f"  {row[0]:30s} {row[1]:20s}")

# 4. Show module names sample
print("\n=== tagente_modulo sample ===")
cur.execute("SELECT id_agente_modulo, nombre, id_agente FROM tagente_modulo WHERE disabled = 0 LIMIT 5")
for row in cur.fetchall():
    print(f"  mod_id={row[0]}, nombre='{row[1]}', agent_id={row[2]}")

cur.close()
conn.close()
