import random
import logging
from datetime import datetime
from typing import Dict, Any

from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import select

from extensions import db, socketio
from models import ShopItem, UserInventory, Transaction, ShopItemType, User, FeedEvent
from gamification import get_or_create_profile

bp_shop = Blueprint('shop', __name__, url_prefix='/api/shop')


# --- Logic Layer: Mystery Box (CD7 - Curiosity) ---

def open_mystery_box(item: ShopItem) -> Dict[str, Any]:
    """
    Алгоритм открытия Mystery Box.
    """
    attributes = item.attributes or {}
    loot_table = attributes.get('loot_table', [])

    if not loot_table:
        return {"name": "Empty Box", "type": "miss", "description": "В коробке было пусто..."}

    population = loot_table
    weights = [entry.get('weight', 1) for entry in loot_table]
    winner_list = random.choices(population, weights=weights, k=1)
    winner = winner_list[0]

    return winner


def _notify_admin_win(user: User, prize_info: dict):
    """
    Уведомление админа о выигрыше через SocketIO и логи.
    """
    msg = f"Пользователь {user.username} выиграл: {prize_info['name']}"

    # Отправка сокета админам (предполагаем наличие комнаты 'admins')
    socketio.emit('admin_notification', {
        'title': 'Победа в Mystery Box',
        'body': msg,
        'user_id': user.id,
        'prize': prize_info
    }, to='admins')

    current_app.logger.warning(f"!!! PRIZE ALERT !!! {msg}")


# --- API Endpoints ---

@bp_shop.route('/list', methods=['GET'])
@jwt_required()
def get_shop_list():
    """
    Витрина магазина (API).
    """
    items = db.session.execute(select(ShopItem)).scalars().all()

    result = []
    for item in items:
        result.append({
            "id": item.id,
            "name": item.name,
            "price": item.price,
            "image_url": item.image_url,
            "type": item.type.value,
            "description": "Испытай удачу!" if item.type == ShopItemType.MYSTERY_BOX else "Полезный предмет"
        })

    return jsonify(result)


@bp_shop.route('/buy', methods=['POST'])
@jwt_required()
def buy_item():
    """
    Покупка предмета (API с JWT).
    """
    user_id = get_jwt_identity()
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json(silent=True) or {}
    item_id = data.get('item_id')

    if not item_id:
        return jsonify({"error": "item_id is required"}), 400

    profile = get_or_create_profile(user)
    item = db.session.get(ShopItem, item_id)

    if not item:
        return jsonify({"error": "Item not found"}), 404

    if profile.coins < item.price:
        return jsonify({
            "error": "Not enough coins",
            "current_balance": profile.coins,
            "required": item.price
        }), 400

    try:
        # Списание средств
        profile.coins -= item.price

        purchase_txn = Transaction(
            user_id=user.id,
            amount=-item.price,
            reason=f"Покупка: {item.name}"
        )
        db.session.add(purchase_txn)

        response_data = {
            "status": "success",
            "item_name": item.name,
            "new_balance": profile.coins,
            "is_mystery": False
        }

        if item.type == ShopItemType.MYSTERY_BOX:
            # === Logic Mystery Box ===
            prize = open_mystery_box(item)
            response_data["is_mystery"] = True
            response_data["prize"] = prize

            prize_type = prize.get("type")

            if prize_type == "coins":
                amount = prize.get("amount", 0)
                profile.coins += amount

                # Транзакция выигрыша
                prize_txn = Transaction(
                    user_id=user.id,
                    amount=amount,
                    reason=f"Win from {item.name}"
                )
                db.session.add(prize_txn)
                response_data["new_balance"] = profile.coins

            elif prize_type in ["real", "title", "physical"]:
                # Уведомляем админа о любом значимом призе
                _notify_admin_win(user, prize)

                # Добавляем запись в Ленту событий
                feed_event = FeedEvent(
                    user_id=user.id,
                    event_type="SHOP_WIN",
                    message=f"{user.username} выиграл приз: {prize.get('name')}!",
                    meta_data=prize
                )
                db.session.add(feed_event)

                # Отправляем сокет всем (если это крутой приз)
                socketio.emit('feed_update', {
                    'user': user.username,
                    'message': f"Выиграл {prize.get('name')} в Mystery Box!",
                    'type': 'win'
                })

            inventory_item = UserInventory(
                user_id=user.id,
                item_id=item.id,
                is_used=True
            )
            db.session.add(inventory_item)

        else:
            # === Normal Buy ===
            inventory_item = UserInventory(
                user_id=user.id,
                item_id=item.id,
                is_used=False
            )
            db.session.add(inventory_item)

        db.session.commit()
        return jsonify(response_data)

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Shop Error")
        return jsonify({"error": "Transaction failed"}), 500