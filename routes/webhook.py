from flask import Blueprint, request, jsonify
from models.pipeline import Pipeline, PipelineExecution
from services.pipeline_executor import PipelineExecutor
from app import db

webhook_bp = Blueprint('webhook', __name__)

@webhook_bp.route('/<string:pipeline_token>', methods=['POST'])
def trigger_pipeline(pipeline_token):
    # Find pipeline by trigger token
    pipeline = Pipeline.query.filter_by(trigger_token=pipeline_token).first()
    
    if not pipeline or not pipeline.is_active:
        return jsonify({'error': 'Invalid pipeline token or pipeline not active'}), 404
    
    # Get active configuration
    config = pipeline.get_active_configuration()
    if not config:
        return jsonify({'error': 'No active configuration found'}), 400
    
    # Create execution record
    execution = PipelineExecution(
        pipeline_id=pipeline.id,
        configuration_id=config.id,
        trigger_method='webhook',
        status='pending'
    )
    
    db.session.add(execution)
    db.session.commit()
    
    # TODO: Queue the pipeline execution (for now, just return success)
    # executor = PipelineExecutor()
    # executor.execute_async(execution.id)
    
    return jsonify({
        'message': 'Pipeline triggered successfully',
        'execution_id': execution.id,
        'pipeline_name': pipeline.name
    }), 200

