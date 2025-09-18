from datetime import date
from app.services.weather_service import GeoPoint, upsert_weather_history_to_db, upsert_weather_forecast_to_db

sydney = GeoPoint(-33.8688, 151.2093)  # update if your bakery is elsewhere
n_hist = upsert_weather_history_to_db(sydney, start=date(2024,1,1))
n_fcst = upsert_weather_forecast_to_db(sydney)
print("Loaded history rows:", n_hist, "Loaded forecast rows:", n_fcst)
