from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
import mercadopago
from dotenv import load_dotenv

# Cargar variables desde .env
load_dotenv()

# Inicializar la base de datos
db = SQLAlchemy()
migrate = Migrate()

# Inicializar SDK de Mercado Pago
mp_sdk = mercadopago.SDK(os.getenv("MP_ACCESS_TOKEN"))

def create_app():
    app = Flask(__name__)

    # Configuración básica
    app.config['SECRET_KEY'] = 'clave-secreta-mvp'

    # Fallback: si no hay DATABASE_URL, usar SQLite local
    db_url = os.getenv("DATABASE_URL", "sqlite:///outbid.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Inicializar extensiones
    db.init_app(app)
    migrate.init_app(app, db)

    # Importar modelos para que se registren
    from app import models

    # Registrar rutas
    from app.routes.leaderboard import leaderboard_bp
    app.register_blueprint(leaderboard_bp)

    with app.app_context():
        db.create_all()
        _ensure_columns()

    return app


def _ensure_columns():
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if "project" in tables:
        project_cols = {c["name"] for c in inspector.get_columns("project")}
        if "last_rank" not in project_cols:
            db.session.execute(text("ALTER TABLE project ADD COLUMN last_rank INTEGER"))
    if "bid" in tables:
        bid_cols = {c["name"] for c in inspector.get_columns("bid")}
        if "created_at" not in bid_cols:
            col_type = "TIMESTAMP" if db.engine.dialect.name == "postgresql" else "DATETIME"
            db.session.execute(text(f"ALTER TABLE bid ADD COLUMN created_at {col_type}"))
    db.session.commit()

