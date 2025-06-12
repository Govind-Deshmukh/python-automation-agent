from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships - using string references to avoid circular imports
    owned_pipelines = db.relationship('Pipeline', foreign_keys='Pipeline.owner_id', backref='owner', lazy=True)
    
    # For permissions where this user is the permission holder
    pipeline_permissions = db.relationship(
        'PipelinePermission', 
        foreign_keys='PipelinePermission.user_id',
        backref='user', 
        lazy=True
    )
    
    # For permissions granted by this user
    granted_permissions = db.relationship(
        'PipelinePermission',
        foreign_keys='PipelinePermission.granted_by_user_id',
        backref='granted_by_user',
        lazy=True
    )
    
    # For pipeline executions triggered by this user
    triggered_executions = db.relationship(
        'PipelineExecution',
        foreign_keys='PipelineExecution.triggered_by_user_id',
        backref='triggered_by_user',
        lazy=True
    )
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'created_at': self.created_at.isoformat()
        }