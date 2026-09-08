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
sdk = mercadopago.SDK(os.getenv("MP_ACCESS_TOKEN"))

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

    return app

