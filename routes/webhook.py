from flask import Blueprint, request, jsonify
from models.pipeline import Pipeline, PipelineExecution
from services.pipeline_executor import PipelineExecutor
from extensions import db
from datetime import datetime

webhook_bp = Blueprint('webhook', __name__)

@webhook_bp.route('/<string:pipeline_token>', methods=['POST'])
def trigger_pipeline(pipeline_token):
    """Trigger pipeline execution via webhook"""
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
        status='pending',
        started_at=datetime.utcnow()
    )
    
    db.session.add(execution)
    db.session.commit()
    
    # Execute pipeline asynchronously
    try:
        executor = PipelineExecutor()
        executor.execute_async(execution.id)
        
        return jsonify({
            'message': 'Pipeline triggered successfully',
            'execution_id': execution.id,
            'pipeline_name': pipeline.name,
            'status': 'pending'
        }), 200
        
    except Exception as e:
        # Update execution status on error
        execution.status = 'failed'
        execution.error_message = f"Failed to start execution: {str(e)}"
        execution.completed_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'error': 'Failed to start pipeline execution',
            'details': str(e)
        }), 500

@webhook_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'CI/CD Pipeline Platform'
    }), 200