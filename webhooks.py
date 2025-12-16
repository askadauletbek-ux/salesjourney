import logging
from datetime import date
from typing import Dict, List, Any

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import select, and_

from extensions import db
from models import User, AmoCRMUserMap, DailyBuff, BuffType, Transaction, Challenge, ChallengeProgress, ChallengeGoalType
# Импортируем логику из созданного ранее модуля
from gamification import get_or_create_profile, check_streak

bp_webhooks = Blueprint('webhooks', __name__, url_prefix='/api/webhooks')

# Константы
WON_STATUS_ID = 142  # ID статуса "Успешно реализовано"
MIN_CALL_DURATION = 10  # Анти-фрод порог (секунды)


def _parse_amo_hook(form_data: Dict[str, Any], entity: str, event_type: str) -> List[Dict[str, Any]]:
    """
    Парсер входящих данных amoCRM.
    AmoCRM шлет данные в формате x-www-form-urlencoded с ключами вида:
    leads[status][0][id] = 123
    leads[status][0][price] = 1000

    Эта функция превращает их в список словарей.
    """
    results = {}
    prefix = f"{entity}[{event_type}]"

    for key, value in form_data.items():
        if key.startswith(prefix):
            # Парсим ключи вида leads[status][0][field_name]
            try:
                # remove prefix
                rest = key[len(prefix):]  # "[0][id]"
                # split by brackets
                parts = rest.strip("]").split("][")
                if len(parts) >= 2:
                    index = int(parts[0].replace("[", ""))
                    field = parts[1]

                    if index not in results:
                        results[index] = {}
                    results[index][field] = value
            except (ValueError, IndexError):
                continue

    return list(results.values())


def _get_user_by_amo_id(company_id: int, amo_user_id: int) -> User:
    """Находит локального пользователя по ID ответственного в AmoCRM."""
    mapping = db.session.execute(
        select(AmoCRMUserMap).where(
            and_(
                AmoCRMUserMap.amocrm_user_id == amo_user_id,
                # Если у нас мульти-тенант, нужно знать company_id.
                # Для простоты ищем по всем, либо можно передавать ID компании в URL вебхука
                # AmoCRMUserMap.company_id == company_id
            )
        )
    ).scalar_one_or_none()

    if mapping:
        return mapping.platform_user
    return None


@bp_webhooks.route('/amo/events', methods=['POST'])
def handle_amo_events():
    """
    Единая точка входа для вебхуков AmoCRM.
    Обрабатывает leads.status_update и calls.add
    """
    # 1. Логирование (CD2 - Feedback)
    try:
        # request.form - это ImmutableMultiDict, конвертируем для логов
        payload = request.form.to_dict(flat=True)
        current_app.logger.info(f"Incoming AmoCRM Webhook: {payload}")
    except Exception as e:
        current_app.logger.error(f"Error logging webhook: {e}")
        return "Log Error", 200

    # Данные приходят в form-data.
    # AmoCRM может слать несколько сущностей, поэтому проверяем всё.

    processed_count = 0

    # === СЦЕНАРИЙ 1: Обновление статуса сделки (leads[status]) ===
    leads_events = _parse_amo_hook(request.form, 'leads', 'status')
    for lead in leads_events:
        try:
            # Проверяем статус (нам нужен только "Успешно реализовано")
            # Приведение к int обязательно, так как form-data это строки
            if int(lead.get('status_id', 0)) != WON_STATUS_ID:
                continue

            amo_user_id = int(lead.get('responsible_user_id', 0))
            if not amo_user_id:
                continue

            # Ищем пользователя
            # Важно: В реальном продакшене лучше передавать company_id в GET-параметре хука
            # url: /api/webhooks/amo/events?cid=3
            user = _get_user_by_amo_id(None, amo_user_id)
            if not user:
                current_app.logger.warning(f"User not found for Amo ID {amo_user_id}")
                continue

            # Логика начисления
            budget = float(lead.get('price', 0))
            if budget <= 0:
                continue

            _process_won_deal_reward(user, budget, lead.get('id', 'unknown'))
            processed_count += 1

        except Exception as e:
            current_app.logger.exception(f"Error processing lead event: {e}")

    # === СЦЕНАРИЙ 2: Добавление звонка (calls[add]) ===
    calls_events = _parse_amo_hook(request.form, 'calls', 'add')
    for call in calls_events:
        try:
            duration = int(call.get('duration', 0))

            # Анти-фрод: игнорируем короткие звонки
            if duration < MIN_CALL_DURATION:
                continue

            amo_user_id = int(call.get('responsible_user_id', 0))
            user = _get_user_by_amo_id(None, amo_user_id)
            if not user:
                continue

            _process_call_reward(user, duration, call.get('link', 'call'))
            processed_count += 1

        except Exception as e:
            current_app.logger.exception(f"Error processing call event: {e}")

    # AmoCRM ожидает 200 OK, даже если мы упали внутри логики,
    # чтобы не слать повторные хуки бесконечно.
    db.session.commit()
    return jsonify({"status": "ok", "processed": processed_count})


# --- Внутренняя логика начисления (CD2 & CD8) ---

def _process_won_deal_reward(user: User, budget: float, lead_id: str):
    """
    Начисление за успешную сделку с учетом Баффов и Стрика.
    """
    profile = get_or_create_profile(user)

    # 1. Обновляем активность и стрик (CD8 - Avoidance)
    # Важно вызвать ДО расчета, так как стрик может повлиять на бонус
    check_streak(profile)

    # 2. Получаем сегодняшний бафф (CD3 - Choice)
    today = date.today()
    buff_entry = db.session.execute(
        select(DailyBuff).where(
            and_(DailyBuff.user_id == user.id, DailyBuff.date == today)
        )
    ).scalar_one_or_none()

    buff_type = buff_entry.buff_type if buff_entry else None

    # 3. Базовый расчет множителя по стратегии
    multiplier = 1.0
    if buff_type == BuffType.SHARK:
        multiplier = 1.5
    elif buff_type == BuffType.WOODPECKER:
        multiplier = 0.5
    # Если ZEN или нет баффа -> 1.0

    # 4. Бонус за стрик (Long-term commitment)
    if profile.current_streak > 3:
        multiplier *= 1.05  # +5%

    final_amount = int(budget * multiplier)

    # 5. Сохранение
    profile.coins += final_amount
    profile.xp += int(final_amount * 0.1)  # XP даем 10% от суммы коинов (пример)

    txn = Transaction(
        user_id=user.id,
        amount=final_amount,
        reason=f"Сделка #{lead_id} (Бюджет: {budget}, Бафф: {buff_type.value if buff_type else 'None'}, Стрик: {profile.current_streak})"
    )
    db.session.add(txn)

    # --- Challenge Logic ---
    _update_challenge_progress(user, ChallengeGoalType.SALES_VOLUME, int(budget))
    _update_challenge_progress(user, ChallengeGoalType.SALES_COUNT, 1)

    current_app.logger.info(f"Rewarded User {user.id}: +{final_amount} coins for Deal {lead_id}")

def _process_call_reward(user: User, duration: int, source_link: str):
    """
    Начисление за звонок.
    """
    profile = get_or_create_profile(user)

    # 1. Обновляем стрик
    check_streak(profile)

    # 2. Бафф
    today = date.today()
    buff_entry = db.session.execute(
        select(DailyBuff).where(
            and_(DailyBuff.user_id == user.id, DailyBuff.date == today)
        )
    ).scalar_one_or_none()
    buff_type = buff_entry.buff_type if buff_entry else None

    # 3. Расчет (наоборот для звонков)
    # Базовая награда: 1 секунда = 1 коин (условно)
    base_points = duration
    multiplier = 1.0

    if buff_type == BuffType.WOODPECKER:
        multiplier = 1.5  # Дятел эффективен в рутине (звонках)
    elif buff_type == BuffType.SHARK:
        multiplier = 0.5  # Акула не любит мелкую работу

    final_amount = int(base_points * multiplier)

    # 4. Сохранение
    profile.coins += final_amount
    profile.xp += 5  # Фиксированный опыт за звонок

    txn = Transaction(
        user_id=user.id,
        amount=final_amount,
        reason=f"Звонок {duration}с (Бафф: {buff_type.value if buff_type else 'None'})"
    )
    db.session.add(txn)

    # --- Challenge Logic ---
    _update_challenge_progress(user, ChallengeGoalType.CALLS_COUNT, 1)


def _update_challenge_progress(user: User, goal_type: ChallengeGoalType, value: int):
    """
    Обновляет прогресс во всех активных челленджах компании.
    """
    today = date.today()

    # Ищем активные челленджи компании по типу цели
    active_challenges = db.session.execute(
        select(Challenge).where(
            and_(
                Challenge.company_id == user.company_id,
                Challenge.is_active == True,
                Challenge.start_date <= today,
                Challenge.end_date >= today,
                Challenge.goal_type == goal_type
            )
        )
    ).scalars().all()

    for challenge in active_challenges:
        # Ищем или создаем запись прогресса
        progress = db.session.execute(
            select(ChallengeProgress).where(
                and_(
                    ChallengeProgress.challenge_id == challenge.id,
                    ChallengeProgress.user_id == user.id
                )
            )
        ).scalar_one_or_none()

        if not progress:
            progress = ChallengeProgress(challenge_id=challenge.id, user_id=user.id, current_value=0)
            db.session.add(progress)

        progress.current_value += value