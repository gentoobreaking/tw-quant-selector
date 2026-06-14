import os
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_USER"] = "tw-quant"
os.environ["POSTGRES_PASSWORD"] = "tw-quant-PassWd"

from datetime import date
from tw_quant_selector.data.database import Database
from tw_quant_selector.strategies.base import get_strategy, list_strategies, SQLAlchemyDataProvider

db = Database()
dp = SQLAlchemyDataProvider(db)
as_of_date = date(2026, 6, 7)

print("Available strategies:", list_strategies())

for name in list_strategies():
    print(f"Testing {name}...")
    try:
        strat = get_strategy(name)
        # Pass dp if momentum, else db
        if name == "momentum":
            scores = strat.compute_score(["2330"], as_of_date, dp)
        else:
            scores = strat.compute_score(["2330"], as_of_date, db)
        print(f"  Result: {scores}")
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()

db.close()
