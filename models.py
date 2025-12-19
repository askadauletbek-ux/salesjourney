import enum
import uuid
from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import Integer, String, Boolean, ForeignKey, DateTime, Date, BigInteger, Enum, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import generate_password_hash, check_password_hash  # <--- НОВОЕ: Для безопасности

from extensions import db
from flask_login import UserMixin


# --- Enums ---

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    PARTNER = "PARTNER"
    COMPANY_OWNER = "COMPANY_OWNER"
    MANAGER = "MANAGER"
    EMPLOYEE = "EMPLOYEE"


class BuffType(str, enum.Enum):
    SHARK = "SHARK"
    WOODPECKER = "WOODPECKER"
    ZEN = "ZEN"


class ShopItemType(str, enum.Enum):
    REAL = "REAL"
    DIGITAL = "DIGITAL"
    MYSTERY_BOX = "MYSTERY_BOX"


class ChallengeGoalType(str, enum.Enum):
    SALES_COUNT = "SALES_COUNT"
    SALES_VOLUME = "SALES_VOLUME"
    CALLS_COUNT = "CALLS_COUNT"


class ChallengeMode(str, enum.Enum):
    PERSONAL = "PERSONAL"
    TEAM = "TEAM"
# --- Core Identity Models ---

class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=True)  # nullable, т.к. вход по email
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)

    # НОВОЕ: Поля для аватара (храним в БД)
    avatar_data: Mapped[Optional[bytes]] = mapped_column(db.LargeBinary, nullable=True)
    avatar_mimetype: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # НОВОЕ: Храним не пароль, а его хеш
    password_hash: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.EMPLOYEE, nullable=False)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey('companies.id'), nullable=True)

    # Связи
    company: Mapped[Optional["Company"]] = relationship(back_populates="employees", foreign_keys=[company_id])
    partner_profile: Mapped[Optional["PartnerUser"]] = relationship(back_populates="user", uselist=False,
                                                                    cascade="all, delete-orphan")
    gamification_profile: Mapped[Optional["GamificationProfile"]] = relationship(back_populates="user", uselist=False,
                                                                                 cascade="all, delete-orphan")
    achievements: Mapped[List["UserAchievement"]] = relationship(cascade="all, delete-orphan")

    # Геймификация Backrefs
    buffs: Mapped[List["DailyBuff"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    transactions: Mapped[List["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    inventory: Mapped[List["UserInventory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    amo_maps: Mapped[List["AmoCRMUserMap"]] = relationship(back_populates="platform_user", cascade="all, delete-orphan")
    challenge_progress: Mapped[List["ChallengeProgress"]] = relationship(back_populates="user",
                                                                         cascade="all, delete-orphan")

    # Лента и соц. взаимодействие
    posts: Mapped[List["Post"]] = relationship(back_populates="author", cascade="all, delete-orphan")
    comments: Mapped[List["Comment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    likes: Mapped[List["Like"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"

    # --- Методы безопасности ---
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)


class PartnerUser(db.Model):
    __tablename__ = 'partner_users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="partner_profile")
    companies: Mapped[List["Company"]] = relationship(back_populates="owner_partner")


class Company(db.Model):
    __tablename__ = 'companies'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    invite_code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False,
                                             default=lambda: str(uuid.uuid4())[:8].upper())

    owner_partner_id: Mapped[int] = mapped_column(ForeignKey('partner_users.id'), nullable=False)
    owner_partner: Mapped["PartnerUser"] = relationship(back_populates="companies")

    employees: Mapped[List["User"]] = relationship(back_populates="company", foreign_keys="[User.company_id]")
    amocrm_connection: Mapped[Optional["AmoCRMConnection"]] = relationship(back_populates="company", uselist=False,
                                                                           cascade="all, delete-orphan")
    user_maps: Mapped[List["AmoCRMUserMap"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    challenges: Mapped[List["Challenge"]] = relationship(back_populates="company", cascade="all, delete-orphan")

# --- Остальные модели (AmoCRM, Gamification и др.) оставляем без изменений ---
# (Ниже приведен сокращенный код для контекста, он не менялся структурно,
#  но важен для работы приложения. Убедитесь, что остальные классы:
#  AmoCRMConnection, AmoCRMUserMap, GamificationProfile, DailyBuff,
#  Challenge, FeedEvent, Transaction, ShopItem, UserInventory
#  остались в файле как были.)

class AmoCRMConnection(db.Model):
    __tablename__ = 'amocrm_connections'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), unique=True, nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=True)
    base_domain: Mapped[str] = mapped_column(String(255), nullable=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=True)
    client_secret: Mapped[str] = mapped_column(String(255), nullable=True)
    last_sync_at: Mapped[int] = mapped_column(BigInteger, nullable=True)
    company: Mapped["Company"] = relationship(back_populates="amocrm_connection")


class AmoCRMUserMap(db.Model):
    __tablename__ = 'amocrm_user_maps'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False)
    platform_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    amocrm_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company: Mapped["Company"] = relationship(back_populates="user_maps")
    platform_user: Mapped["User"] = relationship(back_populates="amo_maps")


class Achievement(db.Model):
    __tablename__ = 'achievements'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(255))
    icon_code: Mapped[str] = mapped_column(String(50))  # Например: 'fa-trophy'
    condition_type: Mapped[str] = mapped_column(String(50))  # 'calls', 'mins', 'conv'
    condition_value: Mapped[int] = mapped_column(Integer)


class UserAchievement(db.Model):
    __tablename__ = 'user_achievements'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    achievement_id: Mapped[int] = mapped_column(ForeignKey('achievements.id'))
    earned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    achievement: Mapped["Achievement"] = relationship()


class GamificationProfile(db.Model):
    __tablename__ = 'gamification_profiles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True, nullable=False)
    coins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_activity_date: Mapped[date] = mapped_column(Date, nullable=True)

    # Поля для отложенного начисления
    last_reward_data: Mapped[Optional[dict]] = mapped_column(db.JSON, nullable=True)
    show_reward_modal: Mapped[bool] = mapped_column(Boolean, default=False)

    # Новое: ID достижения, которое нужно показать в модалке
    pending_achievement_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    user: Mapped["User"] = relationship(back_populates="gamification_profile")


class DailyBuff(db.Model):
    __tablename__ = 'daily_buffs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    buff_type: Mapped[BuffType] = mapped_column(Enum(BuffType), nullable=False)
    user: Mapped["User"] = relationship(back_populates="buffs")


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user: Mapped["User"] = relationship(back_populates="transactions")


class Challenge(db.Model):
    __tablename__ = 'challenges'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Тип цели: Сумма продаж, кол-во звонков и т.д.
    goal_type: Mapped[ChallengeGoalType] = mapped_column(Enum(ChallengeGoalType), nullable=False)
    # Целевое значение (например, 100 звонков или 10 000 000 валюты)
    goal_value: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Режим: Личный или Командный
    mode: Mapped[ChallengeMode] = mapped_column(Enum(ChallengeMode), default=ChallengeMode.PERSONAL, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped["Company"] = relationship(back_populates="challenges")
    progress_records: Mapped[List["ChallengeProgress"]] = relationship(back_populates="challenge",
                                                                       cascade="all, delete-orphan")


class ChallengeProgress(db.Model):
    __tablename__ = 'challenge_progress'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey('challenges.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)

    # Текущее значение прогресса (накопительное)
    current_value: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    challenge: Mapped["Challenge"] = relationship(back_populates="progress_records")
    user: Mapped["User"] = relationship(back_populates="challenge_progress")


class ShopItem(db.Model):
    __tablename__ = 'shop_items'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Ссылка на компанию. Если NULL — товар глобальный (виден всем)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey('companies.id'), nullable=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=True)
    type: Mapped[ShopItemType] = mapped_column(Enum(ShopItemType), default=ShopItemType.REAL)  # Меняем дефолт на REAL
    attributes: Mapped[dict] = mapped_column(db.JSON, nullable=True, default=dict)

    # Связи
    company: Mapped[Optional["Company"]] = relationship(backref="shop_items")

class UserInventory(db.Model):
    __tablename__ = 'user_inventory'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey('shop_items.id'), nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped["User"] = relationship(back_populates="inventory")
    item: Mapped["ShopItem"] = relationship()


class Post(db.Model):
    __tablename__ = 'posts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # НОВОЕ: Поле для хранения самого файла в Postgres
    image_data: Mapped[Optional[bytes]] = mapped_column(db.LargeBinary, nullable=True)
    image_mimetype: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    author: Mapped["User"] = relationship(back_populates="posts")
    company: Mapped["Company"] = relationship()
    comments: Mapped[List["Comment"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    likes: Mapped[List["Like"]] = relationship(back_populates="post", cascade="all, delete-orphan")

class Comment(db.Model):
    __tablename__ = 'comments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey('posts.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    post: Mapped["Post"] = relationship(back_populates="comments")
    user: Mapped["User"] = relationship(back_populates="comments")

class Like(db.Model):
    __tablename__ = 'likes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey('posts.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    post: Mapped["Post"] = relationship(back_populates="likes")
    user: Mapped["User"] = relationship(back_populates="likes")

class FeedEvent(db.Model):
    __tablename__ = 'feed_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False) # Добавлено
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(255), nullable=False)
    meta_data: Mapped[dict] = mapped_column(db.JSON, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user: Mapped["User"] = relationship()
    company: Mapped["Company"] = relationship() # Связь для фильтрации по компании

class DailyStory(db.Model):
    __tablename__ = 'daily_stories'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    story_type: Mapped[str] = mapped_column(String(50), nullable=False) # 'CALLS', 'CONV', 'WINS'
    value: Mapped[float] = mapped_column(db.Float, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False) # За какой день итог

    user: Mapped["User"] = relationship()
    company: Mapped["Company"] = relationship()

class AmoCRMUserDailyStat(db.Model):
    __tablename__ = 'amocrm_user_daily_stats'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)

    calls_count: Mapped[int] = mapped_column(Integer, default=0)
    talk_seconds: Mapped[int] = mapped_column(Integer, default=0)
    leads_created: Mapped[int] = mapped_column(Integer, default=0)
    leads_won: Mapped[int] = mapped_column(Integer, default=0)
    leads_lost: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(backref="daily_stats")

    @property
    def conversion(self):
        # Win Rate: Успешные / (Успешные + Проваленные)
        # Используем (x or 0), чтобы превратить None в 0
        won = self.leads_won or 0
        lost = self.leads_lost or 0

        total_closed = won + lost
        if total_closed > 0:
            return round((won / total_closed) * 100, 1)
        return 0.0

    @property
    def minutes_talked(self):
        return round(self.talk_seconds / 60, 1)