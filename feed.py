import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app, render_template, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db, socketio
from models import Post, Comment, Like, User, FeedEvent, UserRole, Company, DailyStory
from datetime import date, timedelta
from flask import send_file
import io

bp_feed = Blueprint('feed', __name__, url_prefix='/api/feed')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@bp_feed.route('/list', methods=['GET'])
@login_required
def get_feed():
    """Получение объединенной ленты (посты + системные события)"""
    # Исправление: Приоритет параметру из URL (для партнеров), затем профилю юзера
    company_id = request.args.get('company_id', type=int) or current_user.company_id
    if not company_id:
        return jsonify([])

    # Проверка прав: если юзер партнер, он должен владеть этой компанией
    if current_user.role in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
        company = db.session.get(Company, company_id)
        if not company or (current_user.partner_profile and company.owner_partner_id != current_user.partner_profile.id):
            abort(403)

    # Получаем посты
    posts = Post.query.filter_by(company_id=company_id).order_by(Post.created_at.desc()).limit(20).all()
    # Получаем системные события
    events = FeedEvent.query.filter_by(company_id=company_id).order_by(FeedEvent.created_at.desc()).limit(20).all()

    combined_feed = []

    # Исправление: добавляем company_id в фильтр (важно для партнеров)
    company_id = request.args.get('company_id', type=int) or current_user.company_id
    if not company_id:
        return jsonify([])

    for p in posts:
        combined_feed.append({
            'id': f"post_{p.id}",  # Уникальный префикс для ключа Alpine.js
            'type': 'post',
            'author': p.author.username,
            'content': p.content,
            # Ссылка теперь ведет на внутренний API роут
            'image_url': f"/api/feed/image/{p.id}" if p.image_data else None,
            'created_at': p.created_at.isoformat(),
            'likes_count': len(p.likes),
            'is_liked': any(l.user_id == current_user.id for l in p.likes),
            'comments': [{
                'username': c.user.username,
                'text': c.text,
                'created_at': c.created_at.isoformat()
            } for c in p.comments]
        })

    for e in events:
        combined_feed.append({
            'id': f"event_{e.id}",  # Уникальный префикс для ключа Alpine.js
            'type': 'system',
            'event_type': e.event_type,
            'message': e.message,
            'created_at': e.created_at.isoformat(),
            'username': e.user.username if e.user else None
        })

    # Сортировка по дате
    combined_feed.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(combined_feed)

@bp_feed.route('/image/<int:post_id>')
def serve_post_image(post_id):
    """Выдает изображение прямо из базы данных Postgres"""
    post = db.session.get(Post, post_id)
    if not post or not post.image_data:
        abort(404)
    return send_file(
        io.BytesIO(post.image_data),
        mimetype=post.image_mimetype or 'image/jpeg'
    )

@bp_feed.route('/post/create', methods=['POST'])
@login_required
def create_post():
    """Создание поста владельцем компании"""
    if current_user.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
        abort(403)

    # Исправление: Получаем ID компании из формы (у Партнеров current_user.company_id == None)
    company_id = request.form.get('company_id', type=int) or current_user.company_id
    if not company_id:
        return jsonify({'error': 'Company ID is required'}), 400

    content = request.form.get('content')
    if not content:
        return jsonify({'error': 'Content is required'}), 400

    image_data = None
    image_mimetype = None
    if 'image' in request.files:
        file = request.files['image']
        if file and allowed_file(file.filename):
            # Читаем файл в память для сохранения в БД
            image_data = file.read()
            image_mimetype = file.mimetype

    new_post = Post(
        company_id=company_id,
        author_id=current_user.id,
        content=content,
        image_data=image_data,
        image_mimetype=image_mimetype
    )
    db.session.add(new_post)
    db.session.commit()

    # Уведомление через SocketIO
    socketio.emit('new_post', {'author': current_user.username}, room=f"company_{current_user.company_id}")

    return jsonify({'ok': True, 'post_id': new_post.id})


@bp_feed.route('/post/<int:post_id>/like', methods=['POST'])
@login_required
def toggle_like(post_id):
    """Поставить/убрать лайк"""
    like = Like.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    if like:
        db.session.delete(like)
        status = 'unliked'
    else:
        new_like = Like(post_id=post_id, user_id=current_user.id)
        db.session.add(new_like)
        status = 'liked'

    db.session.commit()
    return jsonify({'ok': True, 'status': status})


# SalesJourney/feed.py

@bp_feed.route('/stories', methods=['GET'])
@login_required
def get_stories():
    yesterday = date.today() - timedelta(days=1)
    stories = DailyStory.query.filter_by(company_id=current_user.company_id, date=yesterday).all()

    res = []
    type_map = {
        'CALLS': {'label': 'Король звонков', 'icon': 'fa-phone', 'color': 'cyan'},
        'CONV': {'label': 'Мастер конверсии', 'icon': 'fa-percent', 'color': 'emerald'},
        'WINS': {'label': 'Закрыватор', 'icon': 'fa-money-bill-trend-up', 'color': 'amber'}
    }

    for s in stories:
        tm = type_map.get(s.story_type, type_map['CALLS'])
        res.append({
            'id': s.id,
            'user_id': s.user_id,          # <--- Добавлено: ID пользователя
            'username': s.user.username,
            'has_avatar': bool(s.user.avatar_data), # <--- Добавлено: флаг аватара
            'type_label': tm['label'],
            'icon': tm['icon'],
            'color': tm['color'],
            'value': s.value,
            'unit': '%' if s.story_type == 'CONV' else ''
        })
    return jsonify(res)

@bp_feed.route('/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    """Добавить комментарий"""
    text = request.json.get('text')
    if not text:
        return jsonify({'error': 'Text is required'}), 400

    new_comment = Comment(
        post_id=post_id,
        user_id=current_user.id,
        text=text
    )
    db.session.add(new_comment)
    db.session.commit()

    return jsonify({'ok': True, 'username': current_user.username})