# Импортируем базу и модель
from extensions import db
from models import User

# Находим пользователя
u = User.query.filter_by(email='dauletbekoffa@gmail.com').first()

# Устанавливаем пароль (хеширование произойдет внутри метода)
u.set_password('12345678')
u.must_change_password = False

# Сохраняем
db.session.commit()
exit()