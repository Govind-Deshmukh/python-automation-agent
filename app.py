from flask import Flask
from config import Config
from extensions import db, migrate, jwt
import os

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    
    # Create directories for workspace and logs
    os.makedirs(app.config.get('WORKSPACE_DIR', './workspace'), exist_ok=True)
    os.makedirs(app.config.get('BUILD_LOGS_DIR', './logs/builds'), exist_ok=True)
    
    # Import models here to ensure they are registered with SQLAlchemy
    from models import User, Pipeline, PipelineConfiguration, PipelineExecution, PipelinePermission
    
    # Register blueprints
    from routes.auth import auth_bp
    from routes.pipelines import pipelines_bp
    from routes.webhook import webhook_bp
    from routes.web import web_bp
    from routes.user_management import user_mgmt_bp
    
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(pipelines_bp, url_prefix='/api/pipelines')
    app.register_blueprint(webhook_bp, url_prefix='/webhook')
    app.register_blueprint(web_bp)  # Web routes at root
    app.register_blueprint(user_mgmt_bp, url_prefix='/user-mgmt')
    
    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)