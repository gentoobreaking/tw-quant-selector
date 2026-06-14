import duckdb
con = duckdb.connect("data/tw_quant.duckdb", read_only=True)
print(con.execute("PRAGMA table_info(portfolio);").fetchall())
