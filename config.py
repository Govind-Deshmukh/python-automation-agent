import os
from datetime import timedelta

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///cicd.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'jwt-secret-key'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
    
    # Pipeline execution configuration
    WORKSPACE_DIR = os.environ.get('WORKSPACE_DIR') or './workspace'
    BUILD_LOGS_DIR = os.environ.get('BUILD_LOGS_DIR') or './logs/builds'
    GIT_TIMEOUT = int(os.environ.get('GIT_TIMEOUT', 300))  # 5 minutes default
    MAX_BUILDS_HISTORY = int(os.environ.get('MAX_BUILDS_HISTORY', 50))
    
    # Docker configuration
    DOCKER_ENABLED = os.environ.get('DOCKER_ENABLED', 'true').lower() == 'true'
    DEFAULT_DOCKER_IMAGE = os.environ.get('DEFAULT_DOCKER_IMAGE', 'ubuntu:24.10')