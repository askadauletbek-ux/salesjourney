from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_login import LoginManager  # <--- Добавлен этот импорт
from flask_migrate import Migrate       # <--- И этот, так как Migrate тоже используется ниже

db = SQLAlchemy()
# socketio инициализируется дважды в вашем коде, лучше оставить один раз
socketio = SocketIO(cors_allowed_origins="*")
jwt = JWTManager()
cors = CORS()

login_manager = LoginManager()
partner_login_manager = LoginManager()

migrate = Migrate()