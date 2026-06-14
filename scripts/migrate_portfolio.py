from tw_quant_selector.data.database import Database
db = Database(read_only=False)
try:
    db.execute('ALTER TABLE portfolio ADD COLUMN pl_threshold DOUBLE')
    db.execute('ALTER TABLE portfolio ADD COLUMN pl_percent_threshold DOUBLE')
    print('Migration successful')
except Exception as e:
    print(f'Migration failed (likely already exists): {e}')
