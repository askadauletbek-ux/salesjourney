import random
import logging
from datetime import datetime
from typing import Dict, Any, List

from flask import Blueprint, jsonify, request, current_app, render_template, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from sqlalchemy import select, or_

from extensions import db, socketio
from models import ShopItem, UserInventory, Transaction, ShopItemType, User, FeedEvent, UserRole, Company
from gamification import get_or_create_profile

# Используем префикс /shop для совместимости с шаблонами (render_template)
bp_shop = Blueprint('shop', __name__, url_prefix='/shop')


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


# --- Views & API ---

@bp_shop.route('/', methods=['GET'])
@login_required
def shop_index():
    """
    Главная страница магазина.
    Показывает: Глобальные товары (company_id is None) ИЛИ Товары компании текущего пользователя.
    """
    query_filter = or_(
        ShopItem.company_id.is_(None),
        ShopItem.company_id == current_user.company_id
    )

    # Сортируем: сначала дешевые
    items = db.session.execute(
        select(ShopItem).where(query_filter).order_by(ShopItem.price.asc())
    ).scalars().all()

    # Если запрос пришел от API (например, из мобильного приложения или fetch)
    if request.headers.get('Accept') == 'application/json':
        result = []
        for item in items:
            result.append({
                "id": item.id,
                "name": item.name,
                "price": item.price,
                "image_url": item.image_url,
                "type": item.type.value,
                "company_id": item.company_id,
                "description": "Испытай удачу!" if item.type == ShopItemType.MYSTERY_BOX else "Полезный предмет"
            })
        return jsonify(result)

    # Стандартный рендер шаблона
    return render_template('shop/index.html', items=items, user=current_user)


@bp_shop.route('/buy', methods=['POST'])
@login_required
def buy_item():
    """
    Покупка предмета.
    Принимает JSON: {"item_id": 123}
    """
    user = current_user
    data = request.get_json(silent=True) or {}
    item_id = data.get('item_id')

    if not item_id:
        return jsonify({"error": "item_id is required"}), 400

    profile = get_or_create_profile(user)
    item = db.session.get(ShopItem, item_id)

    if not item:
        return jsonify({"error": "Item not found"}), 404

    # Проверка: товар должен быть доступен пользователю (глобальный или его компании)
    if item.company_id is not None and item.company_id != user.company_id:
        return jsonify({"error": "Item not available for your company"}), 403

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
                # Уведомляем админа
                _notify_admin_win(user, prize)

                # Добавляем запись в Ленту событий
                feed_event = FeedEvent(
                    user_id=user.id,
                    event_type="SHOP_WIN",
                    message=f"{user.username} выиграл приз: {prize.get('name')}!",
                    meta_data=prize
                )
                db.session.add(feed_event)

                # Отправляем сокет всем
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
            # === Normal Buy (Real Item) ===
            inventory_item = UserInventory(
                user_id=user.id,
                item_id=item.id,
                is_used=False  # Реальный товар пока не использован, его нужно "предъявить"
            )
            db.session.add(inventory_item)

            # Уведомляем админа о покупке реального товара (чтобы он его выдал)
            if item.type == ShopItemType.REAL:
                socketio.emit('admin_notification', {
                    'title': 'Покупка в магазине',
                    'body': f"{user.username} купил: {item.name}. Нужно выдать!",
                    'user_id': user.id
                }, to='admins')

        db.session.commit()
        return jsonify(response_data)

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Shop Error")
        return jsonify({"error": "Transaction failed"}), 500


# --- PARTNER MANAGEMENT ROUTES ---

@bp_shop.route('/partner/create', methods=['POST'])
@login_required
def create_item():
    """Создание товара партнером"""
    if current_user.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
        abort(403)

    company_id = request.form.get('company_id', type=int)
    # Проверка прав: Партнер может добавлять только в свои компании
    partner = current_user.partner_profile
    if not partner:
        abort(403)

    company = db.session.get(Company, company_id)
    if not company or company.owner_partner_id != partner.id:
        abort(403)

    name = request.form.get('name')
    price = request.form.get('price', type=int)
    image_url = request.form.get('image_url')

    if not all([name, price]):
        flash('Название и цена обязательны', 'error')
        return redirect(url_for('partner_company', company_id=company_id))

    new_item = ShopItem(
        company_id=company_id,
        name=name,
        price=price,
        image_url=image_url if image_url else None,
        type=ShopItemType.REAL  # Партнеры создают реальные товары
    )
    db.session.add(new_item)
    db.session.commit()

    flash(f'Товар "{name}" добавлен в магазин!', 'success')
    return redirect(url_for('partner_company', company_id=company_id))


@bp_shop.route('/partner/delete/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    """Удаление товара партнером"""
    if current_user.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
        abort(403)

    item = db.session.get(ShopItem, item_id)
    if not item:
        abort(404)

    # Проверка прав: Товар должен принадлежать компании этого партнера
    partner = current_user.partner_profile
    if not partner:
        abort(403)

    if not item.company or item.company.owner_partner_id != partner.id:
        abort(403)

    company_id = item.company_id
    db.session.delete(item)
    db.session.commit()

    flash('Товар удален.', 'info')
    return redirect(url_for('partner_company', company_id=company_id))