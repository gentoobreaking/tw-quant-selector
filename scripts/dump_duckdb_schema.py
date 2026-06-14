import duckdb

con = duckdb.connect('data/tw_quant.duckdb', read_only=True)
tables = con.execute("SHOW TABLES").fetchall()

for t in tables:
    tbl = t[0]
    print(f"\n=== {tbl} ===")
    cols = con.execute(f"PRAGMA table_info(\"{tbl}\")").fetchall()
    for c in cols:
        print(f"  {c[1]:20s}  {c[2]:15s}  nullable={not c[3]}  default={c[4]}")
