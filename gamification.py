from datetime import date, timedelta
from typing import Optional

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from sqlalchemy import select, and_

from extensions import db
from models import User, GamificationProfile, DailyBuff, BuffType, Challenge, ChallengeProgress, ChallengeMode

# Создаем Blueprint
bp_gamification = Blueprint('gamification', __name__, url_prefix='/api/game')


def _get_active_squad_goal(user: User):
    """Вспомогательная функция для расчета общей цели компании"""
    today = date.today()
    if not user.company_id:
        return None

    # Ищем активный челлендж компании
    challenge = db.session.execute(
        select(Challenge).where(
            and_(
                Challenge.company_id == user.company_id,
                Challenge.is_active == True,
                Challenge.start_date <= today,
                Challenge.end_date >= today
            )
        )
    ).scalars().first()

    if not challenge or challenge.goal_value <= 0:
        return None

    # Считаем общий прогресс всех участников
    total_progress = db.session.query(db.func.sum(ChallengeProgress.current_value)) \
                         .filter(ChallengeProgress.challenge_id == challenge.id).scalar() or 0

    percent = int((total_progress / challenge.goal_value) * 100)

    return {
        "name": challenge.name,
        "current": total_progress,
        "target": challenge.goal_value,
        "percent": min(100, percent)
    }

# --- Служебные функции (Logic Layer) ---

def get_or_create_profile(user: User) -> GamificationProfile:
    """
    Получает профиль геймификации пользователя или создает его, если он не существует.
    """
    if user.gamification_profile:
        return user.gamification_profile

    # Инициализация нового профиля
    profile = GamificationProfile(user_id=user.id, coins=0, xp=0, current_streak=0)
    db.session.add(profile)
    # Не делаем commit здесь, чтобы управлять транзакцией на уровне роута
    return profile


def check_streak(profile: GamificationProfile) -> None:
    """
    CD8: Loss & Avoidance.
    Логика расчета серии дней (стрика).
    Должна вызываться при любом значимом активном действии (например, выборе баффа).
    """
    today = date.today()
    last_activity = profile.last_activity_date

    # Если активность уже была сегодня - ничего не меняем
    if last_activity == today:
        return

    # Если последняя активность была вчера - увеличиваем стрик
    if last_activity == today - timedelta(days=1):
        profile.current_streak += 1
    else:
        # Если был пропуск (last_activity < вчера) или это первая активность
        # Стрик сбрасывается (или начинается) с 1, так как действие совершено сегодня
        profile.current_streak = 1

    # Обновляем дату последней активности
    profile.last_activity_date = today


# --- API Endpoints ---

@bp_gamification.route('/buff/choose', methods=['POST'])
@login_required
def choose_buff():
    """
    CD3: Empowerment of Creativity & Feedback.
    Выбор ежедневной стратегии.
    """
    data = request.get_json(silent=True) or {}
    buff_type_str = data.get('buff_type')

    # 1. Валидация входных данных
    if not buff_type_str:
        return jsonify({"error": "buff_type is required"}), 400

    try:
        # Приводим к верхнему регистру для соответствия Enum (SHARK, ZEN...)
        buff_enum = BuffType(buff_type_str.upper())
    except ValueError:
        return jsonify({"error": f"Invalid buff_type. Allowed: {[t.value for t in BuffType]}"}), 400

    profile = get_or_create_profile(current_user)
    today = date.today()

    # 2. Проверка: не выбирал ли уже сегодня?
    existing_buff = db.session.execute(
        select(DailyBuff).where(
            and_(
                DailyBuff.user_id == current_user.id,
                DailyBuff.date == today
            )
        )
    ).scalar_one_or_none()

    if existing_buff:
        return jsonify({"error": "Buff already chosen for today", "current_buff": existing_buff.buff_type.value}), 409

    # 3. Обновляем стрик (так как выбор баффа — это активность)
    check_streak(profile)

    # 4. Сохраняем выбор
    new_buff = DailyBuff(
        user_id=current_user.id,
        date=today,
        buff_type=buff_enum
    )
    db.session.add(new_buff)

    # Здесь можно добавить начисление XP за вход (Daily Login Bonus)
    # profile.xp += 10

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": f"Strategy {buff_enum.value} activated",
        "streak": profile.current_streak
    })


@bp_gamification.route('/status', methods=['GET'])
@login_required
def get_status():
    """
    Получение сводной информации для дэшборда игрока.
    """
    profile = get_or_create_profile(current_user)

    # Нужно проверить, какой бафф выбран сегодня (если выбран)
    today = date.today()
    todays_buff = db.session.execute(
        select(DailyBuff).where(
            and_(
                DailyBuff.user_id == current_user.id,
                DailyBuff.date == today
            )
        )
    ).scalar_one_or_none()

    # Если пользователь зашел, но еще ничего не сделал, стрик в БД может быть "старым".
    # Для отображения в UI мы можем хотеть показать "актуальный" стрик (сброшенный),
    # если он пропустил вчерашний день.
    # Но технически стрик обновляется только при действии.
    # Пока возвращаем то, что в базе.

    # Опционально: Вычисляем "видимый" стрик.
    # Если last_activity < вчера, то визуально стрик уже 0, хотя в базе старое значение до следующего действия.
    display_streak = profile.current_streak
    if profile.last_activity_date and profile.last_activity_date < today - timedelta(days=1):
        display_streak = 0

    # Получаем данные Squad Goal
    squad_data = _get_active_squad_goal(current_user)

    return jsonify({
        "coins": profile.coins,
        "xp": profile.xp,
        "streak": display_streak,
        "level": 1 + (profile.xp // 1000),
        "today_buff": todays_buff.buff_type.value if todays_buff else None,
        "last_activity": profile.last_activity_date.isoformat() if profile.last_activity_date else None,
        "squad": squad_data  # <--- Добавили поле
    })