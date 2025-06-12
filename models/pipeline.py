from extensions import db
from datetime import datetime
import secrets
import string

class Pipeline(db.Model):
    __tablename__ = 'pipelines'
    
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    trigger_token = db.Column(db.String(64), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    configurations = db.relationship('PipelineConfiguration', backref='pipeline', lazy=True)
    executions = db.relationship('PipelineExecution', backref='pipeline', lazy=True)
    permissions = db.relationship('PipelinePermission', backref='pipeline', lazy=True)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not kwargs.get('trigger_token'):
            self.trigger_token = self.generate_trigger_token()
    
    def generate_trigger_token(self):
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(32))
    
    def get_active_configuration(self):
        return PipelineConfiguration.query.filter_by(
            pipeline_id=self.id, 
            is_active=True
        ).first()
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'owner': self.owner.username
        }

class PipelineConfiguration(db.Model):
    __tablename__ = 'pipeline_configurations'
    
    id = db.Column(db.Integer, primary_key=True)
    pipeline_id = db.Column(db.Integer, db.ForeignKey('pipelines.id'), nullable=False)
    yaml_source = db.Column(db.Enum('editor', 'repo', name='yaml_source_enum'), nullable=False)
    yaml_content = db.Column(db.Text)  # For editor-based pipelines
    repo_url = db.Column(db.String(500))  # For repo-based pipelines
    repo_branch = db.Column(db.String(100), default='main')
    yaml_file_path = db.Column(db.String(200), default='pipeline.yml')
    version = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PipelineExecution(db.Model):
    __tablename__ = 'pipeline_executions'
    
    id = db.Column(db.Integer, primary_key=True)
    pipeline_id = db.Column(db.Integer, db.ForeignKey('pipelines.id'), nullable=False)
    configuration_id = db.Column(db.Integer, db.ForeignKey('pipeline_configurations.id'), nullable=False)
    triggered_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    trigger_method = db.Column(db.Enum('manual', 'webhook', name='trigger_method_enum'), nullable=False)
    status = db.Column(db.Enum('pending', 'running', 'success', 'failed', 'cancelled', name='execution_status_enum'), default='pending')
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    logs = db.Column(db.Text)
    error_message = db.Column(db.Text)
    execution_metadata = db.Column(db.JSON)

class PipelinePermission(db.Model):
    __tablename__ = 'pipeline_permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    pipeline_id = db.Column(db.Integer, db.ForeignKey('pipelines.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    permission_level = db.Column(db.Enum('reader', 'developer', 'maintainer', 'owner', name='permission_level_enum'), nullable=False)
    granted_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
