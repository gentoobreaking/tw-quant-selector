import os
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_USER"] = "tw-quant"
os.environ["POSTGRES_PASSWORD"] = "tw-quant-PassWd"

from datetime import date
from decimal import Decimal
from tw_quant_selector.data.database import Database
from tw_quant_selector.backtest.engine import run_backtest
db = Database()

try:
    res = run_backtest(
        db, 
        start_date=date(2026, 6, 4), 
        end_date=date(2026, 6, 5),
        custom_universe=["2330", "2317", "2454"]
    )
    print("Success:", res)
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    db.close()
