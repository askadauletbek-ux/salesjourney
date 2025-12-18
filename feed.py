import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app, render_template, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db, socketio
from models import Post, Comment, Like, User, FeedEvent, UserRole, Company

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

    for p in posts:
        combined_feed.append({
            'id': p.id,
            'type': 'post',
            'author': p.author.username,
            'content': p.content,
            'image_url': p.image_url,
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
            'id': e.id,
            'type': 'system',
            'event_type': e.event_type,
            'message': e.message,
            'created_at': e.created_at.isoformat(),
            'username': e.user.username if e.user else None
        })

    # Сортировка по дате
    combined_feed.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(combined_feed)


@bp_feed.route('/post/create', methods=['POST'])
@login_required
def create_post():
    """Создание поста владельцем компании"""
    if current_user.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
        abort(403)

    # Исправление: Получаем ID компании из формы (для партнеров)
    company_id = request.form.get('company_id', type=int) or current_user.company_id
    if not company_id:
        return jsonify({'error': 'Company ID is required'}), 400

    # Проверка владения компанией
    company = db.session.get(Company, company_id)
    if not company or (current_user.partner_profile and company.owner_partner_id != current_user.partner_profile.id):
        abort(403)

    content = request.form.get('content')
    if not content:
        return jsonify({'error': 'Content is required'}), 400

    image_url = None
    if 'image' in request.files:
        file = request.files['image']
        if file and allowed_file(file.filename):
            filename = secure_filename(f"{uuid.uuid4().hex}_{file.filename}")
            upload_path = os.path.join(current_app.root_path, 'static/uploads/feed')
            os.makedirs(upload_path, exist_ok=True)
            file.save(os.path.join(upload_path, filename))
            image_url = f"/static/uploads/feed/{filename}"

    new_post = Post(
        company_id=company_id,  # Используем проверенный ID
        author_id=current_user.id,
        content=content,
        image_url=image_url
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