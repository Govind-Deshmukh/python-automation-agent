from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from config import Config
import functools

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()

def login_required(f):
    """Decorator to require login for web routes"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('web.login'))
        return f(*args, **kwargs)
    return decorated_function

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    
    # Register blueprints
    from routes.auth import auth_bp
    from routes.pipelines import pipelines_bp
    from routes.webhook import webhook_bp
    from routes.web import web_bp
    
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(pipelines_bp, url_prefix='/api/pipelines')
    app.register_blueprint(webhook_bp, url_prefix='/webhook')
    app.register_blueprint(web_bp)  # Web routes at root
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
