from pathlib import Path
from app.pipeline import ingest_sales, generate_forecast

errs = ingest_sales(Path("data/sample_sales.csv"))
print("ingest errors:", errs)

df = generate_forecast()
print(df.head())
