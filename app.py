import os
import uuid
import secrets
from datetime import date  # <--- Добавлен этот импорт
from flask import Flask, render_template, redirect, url_for, request, flash, abort, jsonify
from flask_login import login_user, logout_user, current_user, login_required
from sqlalchemy import or_
from extensions import socketio
from sqlalchemy import select, and_
from flask import send_file
import io

# Импорт расширений
from extensions import db, login_manager

# Импорт моделей
from models import User, GamificationProfile, ShopItem, ShopItemType, UserRole, Company, PartnerUser, AmoCRMUserDailyStat, AmoCRMUserMap, DailyBuff, BuffType
# Импорт модулей (Blueprints)
from amocrm_integration import bp_amocrm_company_api, bp_amocrm_pages
from gamification import bp_gamification
from webhooks import bp_webhooks
from shop import bp_shop
from feed import bp_feed


def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///sales_journey.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    ADMIN_PATH = os.getenv("ADMIN_PATH", "admin")

    from apscheduler.schedulers.background import BackgroundScheduler
    from amocrm_integration import run_nightly_reward_calculation, issue_daily_rewards

    # --- Инициализация расширений ---
    db.init_app(app)
    login_manager.init_app(app)
    socketio.init_app(app)

    # Планировщик задач
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=run_nightly_reward_calculation, trigger="cron", hour=0, minute=1)
    scheduler.add_job(func=issue_daily_rewards, trigger="cron", hour=8, minute=0)
    scheduler.start()

    login_manager.login_view = 'login'

    # --- User Loader (для Flask-Login) ---
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    app.register_blueprint(bp_gamification)
    app.register_blueprint(bp_webhooks)
    app.register_blueprint(bp_shop)
    app.register_blueprint(bp_feed)
    app.register_blueprint(bp_amocrm_company_api)
    app.register_blueprint(bp_amocrm_pages)

    # ==========================
    # PUBLIC ROUTES
    # ==========================

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return render_template('landing.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """
        Вход по Email или Username (identity)
        """
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            identity = request.form.get('identity') or request.form.get('email')
            password = request.form.get('password')
            remember = True if request.form.get('remember') else False

            # Поиск пользователя: Email ИЛИ Username
            user = User.query.filter(
                or_(User.email == identity, User.username == identity)
            ).first()

            if not user or not user.check_password(password):
                flash('Неверный логин или пароль.', 'error')
                return redirect(url_for('login'))

            login_user(user, remember=remember)
            return redirect(url_for('dashboard'))

        return render_template('auth/login.html')

    @app.route('/account/password/change', methods=['GET', 'POST'])
    @login_required
    def force_password_change():
        if request.method == 'GET':
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            if not current_user.must_change_password:
                return redirect(url_for('dashboard'))

            p1 = request.form.get('password')
            p2 = request.form.get('password2')

            if not p1 or len(p1) < 8:
                flash("Пароль должен быть минимум 8 символов.", "warning")
                return redirect(url_for('dashboard'))

            if p1 != p2:
                flash("Пароли не совпадают.", "error")
                return redirect(url_for('dashboard'))

            current_user.set_password(p1)
            current_user.must_change_password = False
            db.session.commit()

            flash("Пароль успешно обновлён! ✅", "success")

            if current_user.role in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
                return redirect(url_for('partner_companies'))
            return redirect(url_for('dashboard'))

        return redirect(url_for('dashboard'))

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            username = request.form.get('username')
            invite_code = request.form.get('company_code')

            if not all([email, password, username, invite_code]):
                flash("Пожалуйста, заполните все поля.", "warning")
                return redirect(url_for('register'))

            company = Company.query.filter_by(invite_code=invite_code.upper()).first()
            if not company:
                flash("Неверный код компании.", "error")
                return redirect(url_for('register'))

            # 1. Проверка Email
            if User.query.filter_by(email=email).first():
                flash("Email уже зарегистрирован.", "error")
                return redirect(url_for('register'))

            # 2. Проверка Username (ИСПРАВЛЕНИЕ)
            if User.query.filter_by(username=username).first():
                flash("Это имя пользователя уже занято. Пожалуйста, выберите другое.", "error")
                return redirect(url_for('register'))

            try:
                new_user = User(
                    username=username,
                    email=email,
                    role=UserRole.EMPLOYEE,
                    company_id=company.id
                )
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.flush()

                profile = GamificationProfile(user_id=new_user.id, coins=0, xp=0, current_streak=0)
                db.session.add(profile)

                db.session.commit()

                login_user(new_user)
                flash("Регистрация успешна! Добро пожаловать.", "success")
                return redirect(url_for('dashboard'))

            except Exception as e:
                db.session.rollback()
                flash(f"Ошибка регистрации: {e}", "error")
                return redirect(url_for('register'))

        return render_template('auth/register.html')

    @app.route('/feed')
    @login_required
    def feed_page():
        return render_template('feed.html')

    @app.route('/user/<int:user_id>')
    @login_required
    def user_profile(user_id):
        u = db.session.get(User, user_id)
        if not u or u.company_id != current_user.company_id: abort(404)
        stat = db.session.execute(select(AmoCRMUserDailyStat).where(and_(AmoCRMUserDailyStat.user_id == user_id,
                                                                         AmoCRMUserDailyStat.date == date.today()))).scalar_one_or_none()
        return render_template('user_profile.html', target_user=u, stats=stat)


    @app.route('/logout')
    def logout():
        logout_user()
        flash("Вы вышли из системы.", "info")
        return redirect(url_for('index'))

    # ==========================
    # PROTECTED ROUTES
    # ==========================

    @app.route('/dashboard')
    @login_required
    def dashboard():
        if current_user.role in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
            return redirect(url_for('partner_companies'))
        elif current_user.role == UserRole.SUPER_ADMIN:
            return redirect(url_for('super_admin_panel'))

        if not current_user.gamification_profile:
            profile = GamificationProfile(user_id=current_user.id, coins=0, xp=0)
            db.session.add(profile)
            db.session.commit()

        # --- Сбор статистики AmoCRM ---
        today = date.today()
        is_amo_linked = False
        if current_user.company_id:
            mapping = db.session.execute(
                select(AmoCRMUserMap).where(
                    and_(
                        AmoCRMUserMap.platform_user_id == current_user.id,
                        AmoCRMUserMap.company_id == current_user.company_id
                    )
                )
            ).scalar_one_or_none()
            if mapping:
                is_amo_linked = True

        daily_stat = db.session.execute(
            select(AmoCRMUserDailyStat).where(
                and_(
                    AmoCRMUserDailyStat.user_id == current_user.id,
                    AmoCRMUserDailyStat.date == today
                )
            )
        ).scalar_one_or_none()

        if not daily_stat:
            # Пустышка для корректного отображения
            daily_stat = AmoCRMUserDailyStat(
                calls_count=0,
                talk_seconds=0,
                leads_created=0,
                leads_won=0,
                leads_lost=0,  # <--- ДОБАВЛЕНО ЭТО ПОЛЕ
                updated_at=None
            )

            # Проверка наличия награды для окна поздравления
            reward_info = None
            suggested_items = []

        if current_user.gamification_profile and current_user.gamification_profile.show_reward_modal:
                reward_info = current_user.gamification_profile.last_reward_data
                current_user.gamification_profile.show_reward_modal = False
                db.session.commit()

                # Подбираем товары из магазина, на которые теперь хватает коинов
                suggested_items = ShopItem.query.filter(
                    and_(
                        or_(ShopItem.company_id == current_user.company_id, ShopItem.company_id.is_(None)),
                        ShopItem.price <= current_user.gamification_profile.coins
                    )
                ).order_by(ShopItem.price.desc()).limit(3).all()

        return render_template('dashboard.html',
                                   user=current_user,
                                   amo_stat=daily_stat,
                                   is_amo_linked=is_amo_linked,
                                   daily_reward=reward_info,
                                   suggestions=suggested_items)

    # ==========================
    # PARTNER ROUTES
    # ==========================

    @app.route('/partner/companies')
    @login_required
    def partner_companies():
        if current_user.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
            abort(403)

        partner = current_user.partner_profile
        companies = partner.companies if partner else []
        return render_template('partner_companies.html', companies=companies)

    @app.route('/leaderboard')
    @login_required
    def leaderboard():
        # Получаем сотрудников компании, сортируем по XP (от большего к меньшему)
        # Если у пользователя нет компании, список будет пуст
        employees = []
        if current_user.company_id:
            employees = User.query.filter_by(company_id=current_user.company_id) \
                .join(GamificationProfile) \
                .order_by(GamificationProfile.xp.desc()) \
                .limit(50) \
                .all()

        return render_template('leaderboard.html', employees=employees)

    @app.route('/partner/company/<int:company_id>')
    @login_required
    def partner_company(company_id: int):
        if current_user.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER]:
            abort(403)

        partner = current_user.partner_profile
        if not partner:
            abort(403)

        company = Company.query.get_or_404(company_id)
        if company.owner_partner_id != partner.id:
            abort(403)

        employees = User.query.filter_by(company_id=company.id).all()

        # --- Сбор статистики и расчет очков ---
        today = date.today()
        employees_data = []

        for emp in employees:
            # 1. Получаем выбранную стратегию (Buff)
            buff_entry = db.session.execute(
                select(DailyBuff).where(
                    and_(DailyBuff.user_id == emp.id, DailyBuff.date == today)
                )
            ).scalar_one_or_none()
            current_buff = buff_entry.buff_type if buff_entry else None

            # 2. Получаем статистику звонков/продаж
            stats = db.session.execute(
                select(AmoCRMUserDailyStat).where(
                    and_(AmoCRMUserDailyStat.user_id == emp.id, AmoCRMUserDailyStat.date == today)
                )
            ).scalar_one_or_none()

            calls = stats.calls_count if stats else 0
            sales = stats.leads_won if stats else 0

            # 3. Расчет очков (XP) по правилам стратегий
            # База: Звонок = 10 XP, Продажа = 500 XP
            score = 0
            if current_buff == BuffType.WOODPECKER:
                # Дятел: x2 за звонки, x0.5 за продажи
                score = (calls * 20) + (sales * 250)
            elif current_buff == BuffType.SHARK:
                # Акула: x0.5 за звонки, x2 за продажи
                score = (calls * 5) + (sales * 1000)
            elif current_buff == BuffType.ZEN:
                # Дзен: Стандарт + бонус 200 XP за спокойствие
                score = (calls * 10) + (sales * 500) + 200
            else:
                # Стандарт (Не выбрано)
                score = (calls * 10) + (sales * 500)

            # Бонус за стрик > 3 дней (+5%)
            streak = emp.gamification_profile.current_streak if emp.gamification_profile else 0
            has_streak_bonus = False
            if streak > 3:
                score *= 1.05
                has_streak_bonus = True

            employees_data.append({
                'user': emp,
                'buff': current_buff,
                'calls': calls,
                'sales': sales,
                'score': int(score),
                'streak': streak,
                'has_bonus': has_streak_bonus
            })

        # Сортировка: Лидеры по очкам вверху
        employees_data.sort(key=lambda x: x['score'], reverse=True)

        return render_template('partner_company.html', company=company, employees_data=employees_data)

    # ==========================
    # SUPER ADMIN LOGIC
    # ==========================

    def super_admin_panel():
        if current_user.role != UserRole.SUPER_ADMIN:
            abort(403)

        stats = {
            'total_companies': Company.query.count(),
            'total_users': User.query.count(),
            'total_partners': User.query.filter(User.role.in_([UserRole.PARTNER, UserRole.COMPANY_OWNER])).count()
        }

        partners = User.query.filter(User.role.in_([UserRole.PARTNER, UserRole.COMPANY_OWNER])).all()
        all_companies = Company.query.all()

        return render_template('admin_panel.html', stats=stats, partners=partners, all_companies=all_companies)

    def create_wizard():
        if current_user.role != UserRole.SUPER_ADMIN:
            abort(403)

        owner_email = request.form.get('owner_email')
        owner_name = request.form.get('owner_name')
        company_name = request.form.get('company_name')

        if not all([owner_email, company_name]):
            flash("Ошибка: Заполните обязательные поля.", "warning")
            return redirect(url_for('super_admin_panel'))

        try:
            temp_password = None
            owner = User.query.filter_by(email=owner_email).first()

            if not owner:
                temp_password = secrets.token_urlsafe(8)
                owner = User(
                    username=owner_name or owner_email.split('@')[0],
                    email=owner_email,
                    role=UserRole.PARTNER
                )
                owner.set_password(temp_password)
                owner.must_change_password = True
                db.session.add(owner)
                db.session.flush()
                flash(f"Создан новый партнер. Временный пароль: {temp_password}", "info")
            else:
                if owner.role not in [UserRole.PARTNER, UserRole.COMPANY_OWNER, UserRole.SUPER_ADMIN]:
                    owner.role = UserRole.PARTNER
                    flash(f"Пользователь {owner.email} повышен до статуса ПАРТНЕР.", "info")

            partner = owner.partner_profile
            if not partner:
                partner = PartnerUser(user_id=owner.id)
                db.session.add(partner)
                db.session.flush()

            slug = str(uuid.uuid4())[:8]
            new_company = Company(
                name=company_name,
                slug=slug,
                owner_partner_id=partner.id
            )
            db.session.add(new_company)
            db.session.commit()

            flash(f"Компания '{company_name}' успешно создана!", "success")

        except Exception as e:
            db.session.rollback()
            flash(f"Ошибка при создании: {str(e)}", "error")

        return redirect(url_for('super_admin_panel'))

    @app.route('/api/user/avatar/<int:user_id>')
    def serve_avatar(user_id):
        """Выдает аватар пользователя из БД"""
        user = db.session.get(User, user_id)
        if not user or not user.avatar_data:
            # Если аватара нет, можно вернуть 404 или дефолтную заглушку
            abort(404)
        return send_file(
            io.BytesIO(user.avatar_data),
            mimetype=user.avatar_mimetype or 'image/png'
        )

    @app.route('/api/user/profile/update', methods=['POST'])
    @login_required
    def update_profile():
        """Обновление имени и аватара"""
        new_username = request.form.get('username')
        avatar_file = request.files.get('avatar')

        if new_username:
            # Проверка на уникальность (опционально)
            current_user.username = new_username

        if avatar_file and avatar_file.filename != '':
            current_user.avatar_data = avatar_file.read()
            current_user.avatar_mimetype = avatar_file.mimetype

        db.session.commit()
        return jsonify({"ok": True, "username": current_user.username})

    @app.route(f'/{ADMIN_PATH}/delete/company/<int:company_id>', methods=['POST'])
    @login_required
    def delete_company(company_id):
        if current_user.role != UserRole.SUPER_ADMIN:
            abort(403)

        comp = db.session.get(Company, company_id)
        if comp:
            try:
                company_name = comp.name
                db.session.delete(comp)
                db.session.commit()
                flash(f"Компания '{company_name}' удалена.", "info")
            except Exception as e:
                db.session.rollback()
                flash(f"Ошибка удаления: {e}", "error")
        else:
            flash("Компания не найдена.", "warning")

        return redirect(url_for('super_admin_panel'))

    app.add_url_rule(f'/{ADMIN_PATH}/panel', endpoint='super_admin_panel',
                     view_func=login_required(super_admin_panel), methods=['GET'])

    app.add_url_rule(f'/{ADMIN_PATH}/create_wizard', endpoint='create_wizard',
                     view_func=login_required(create_wizard), methods=['POST'])

    with app.app_context():
        db.create_all()

        admin_user = User.query.filter_by(email='admin').first()
        if not admin_user:
            admin_user = User(
                username='admin',
                email='admin',
                role=UserRole.SUPER_ADMIN,
                must_change_password=False
            )
            admin_user.set_password('admin')
            db.session.add(admin_user)
        else:
            admin_user.role = UserRole.SUPER_ADMIN
            admin_user.must_change_password = False

        if not ShopItem.query.first():
            item1 = ShopItem(name="Mystery Box", price=50, type=ShopItemType.MYSTERY_BOX,
                             attributes={
                                 "loot_table": [{"name": "100 Gold", "type": "coins", "amount": 100, "weight": 50}]})
            item2 = ShopItem(name="Отгул", price=1000, type=ShopItemType.REAL)
            db.session.add_all([item1, item2])

        db.session.commit()

    return app


if __name__ == '__main__':
    app = create_app()
    # Используем socketio.run вместо app.run для поддержки веб-сокетов
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True, allow_unsafe_werkzeug=True)
