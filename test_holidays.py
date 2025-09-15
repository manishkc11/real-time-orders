from app.services.holiday_service import HolidayScope, upsert_holidays_to_db
n = upsert_holidays_to_db(HolidayScope(country="AU", subdiv="NSW", years=[2024, 2025, 2026]))
print("Holidays inserted:", n)
