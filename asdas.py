import sqlite3
import os

# Путь к базе (проверяем оба варианта)
db_path = 'instance/sales_journey.db'
if not os.path.exists(db_path):
    db_path = 'sales_journey.db'

print(f"Connecting to: {db_path}")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Добавляем колонку company_id
    cursor.execute("ALTER TABLE shop_items ADD COLUMN company_id INTEGER REFERENCES companies(id)")
    conn.commit()
    print("✅ Колонка company_id успешно добавлена!")
except sqlite3.OperationalError as e:
    print(f"⚠️ Ошибка (возможно колонка уже есть): {e}")

conn.close()