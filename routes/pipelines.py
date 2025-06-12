from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.pipeline import Pipeline, PipelineConfiguration
from models.user import User
from app import db

pipelines_bp = Blueprint('pipelines', __name__)

@pipelines_bp.route('/', methods=['GET'])
@jwt_required()
def get_pipelines():
    user_id = get_jwt_identity()
    pipelines = Pipeline.query.filter_by(owner_id=user_id).all()
    return jsonify([pipeline.to_dict() for pipeline in pipelines]), 200

@pipelines_bp.route('/', methods=['POST'])
@jwt_required()
def create_pipeline():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    if not data or not data.get('name'):
        return jsonify({'error': 'Pipeline name is required'}), 400
    
    # Create pipeline
    pipeline = Pipeline(
        owner_id=user_id,
        name=data['name'],
        description=data.get('description', '')
    )
    
    db.session.add(pipeline)
    db.session.flush()  # To get the pipeline ID
    
    # Create initial configuration
    config_data = data.get('configuration', {})
    configuration = PipelineConfiguration(
        pipeline_id=pipeline.id,
        yaml_source=config_data.get('yaml_source', 'editor'),
        yaml_content=config_data.get('yaml_content'),
        repo_url=config_data.get('repo_url'),
        repo_branch=config_data.get('repo_branch', 'main'),
        yaml_file_path=config_data.get('yaml_file_path', 'pipeline.yml')
    )
    
    db.session.add(configuration)
    db.session.commit()
    
    return jsonify({
        'message': 'Pipeline created successfully',
        'pipeline': pipeline.to_dict(),
        'trigger_token': pipeline.trigger_token
    }), 201

@pipelines_bp.route('/<int:pipeline_id>', methods=['GET'])
@jwt_required()
def get_pipeline(pipeline_id):
    user_id = get_jwt_identity()
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=user_id).first()
    
    if not pipeline:
        return jsonify({'error': 'Pipeline not found'}), 404
    
    config = pipeline.get_active_configuration()
    
    response = pipeline.to_dict()
    if config:
        response['configuration'] = {
            'yaml_source': config.yaml_source,
            'yaml_content': config.yaml_content,
            'repo_url': config.repo_url,
            'repo_branch': config.repo_branch,
            'yaml_file_path': config.yaml_file_path
        }
    
    return jsonify(response), 200
