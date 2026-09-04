from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Inicializar la base de datos
db = SQLAlchemy()

def create_app():
    app = Flask(__name__)

    # Configuración básica
    app.config['SECRET_KEY'] = 'clave-secreta-mvp'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///outbid.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Inicializar extensiones
    db.init_app(app)

    # Importar modelos para que se registren
    from app import models

    # Registrar rutas
    from app.routes.leaderboard import leaderboard_bp
    app.register_blueprint(leaderboard_bp)

    return app
