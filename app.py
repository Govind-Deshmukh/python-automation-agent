#!/usr/bin/env python3
"""
Minimal CI/CD Pipeline Platform
Main application runner that provides both web interface and webhook endpoints
"""

import os
import sys
import logging
from flask import Flask
from config import Config

def setup_logging():
    """Setup logging configuration"""
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def create_app():
    """Create and configure Flask application"""
    setup_logging()
    
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Initialize extensions
    from extensions import db, migrate, jwt
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    
    # Create necessary directories
    os.makedirs(app.config.get('WORKSPACE_DIR', './workspace'), exist_ok=True)
    os.makedirs(app.config.get('BUILD_LOGS_DIR', './logs/builds'), exist_ok=True)
    os.makedirs('./logs', exist_ok=True)
    
    # Import models to ensure they are registered
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
    
    # Create database tables
    with app.app_context():
        db.create_all()
        app.logger.info("Database tables created/verified")
    
    return app

def main():
    """Main application entry point"""
    print("🚀 Starting Minimal CI/CD Pipeline Platform...")
    
    # Create the Flask application
    app = create_app()
    
    # Configuration summary
    print("\n" + "="*60)
    print("📋 CI/CD PLATFORM CONFIGURATION")
    print("="*60)
    print(f"🌐 Web Interface: http://localhost:{app.config.get('PORT', 5000)}")
    print(f"📡 Webhook Endpoint: http://localhost:{app.config.get('PORT', 5000)}/webhook/<token>")
    print(f"🔧 API Endpoint: http://localhost:{app.config.get('PORT', 5000)}/api")
    print(f"📁 Workspace Directory: {app.config.get('WORKSPACE_DIR', './workspace')}")
    print(f"📜 Build Logs Directory: {app.config.get('BUILD_LOGS_DIR', './logs/builds')}")
    print(f"🐳 Docker Enabled: {app.config.get('DOCKER_ENABLED', True)}")
    print(f"🗄️  Database: {app.config.get('SQLALCHEMY_DATABASE_URI', 'sqlite:///cicd.db')}")
    print("="*60)
    
    print("\n📖 Getting Started:")
    print("1. Open your browser and go to the web interface URL above")
    print("2. Create an account by clicking 'Sign up here'")
    print("3. Create your first pipeline from the dashboard")
    print("4. Configure webhooks using the provided webhook URL")
    print("\n🎯 Platform is ready! Press Ctrl+C to stop.\n")
    
    # Get configuration
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    try:
        app.run(host=host, port=port, debug=debug, threaded=True)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down CI/CD Pipeline Platform...")
        app.logger.info("Platform shutdown requested")
    except Exception as e:
        print(f"\n❌ Failed to start platform: {e}")
        app.logger.error(f"Failed to start platform: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()