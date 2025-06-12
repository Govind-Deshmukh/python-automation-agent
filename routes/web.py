from flask import Blueprint, request, jsonify, current_app
from models.pipeline import Pipeline, PipelineExecution
from services.pipeline_executor import PipelineExecutor
from extensions import db
from datetime import datetime
import json
import hmac
import hashlib
import logging

webhook_bp = Blueprint('webhook', __name__)

def verify_github_signature(payload_body, secret_token, signature_header):
    """Verify GitHub webhook signature"""
    if not secret_token:
        return True  # No verification if no secret is configured
    
    if not signature_header:
        return False
    
    # GitHub sends signature as "sha256=<hash>"
    if not signature_header.startswith('sha256='):
        return False
    
    signature = signature_header[7:]  # Remove "sha256=" prefix
    
    # Calculate expected signature
    expected_signature = hmac.new(
        secret_token.encode('utf-8'),
        payload_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)

def extract_repository_info(payload):
    """Extract repository information from webhook payload"""
    try:
        repo_data = {}
        
        # GitHub webhook format
        if 'repository' in payload:
            repository = payload['repository']
            repo_data.update({
                'name': repository.get('name'),
                'full_name': repository.get('full_name'),
                'clone_url': repository.get('clone_url'),
                'ssh_url': repository.get('ssh_url'),
                'default_branch': repository.get('default_branch', 'main'),
                'ref': payload.get('ref', '').replace('refs/heads/', ''),
                'commits': payload.get('commits', []),
                'pusher': payload.get('pusher', {}).get('name', 'unknown'),
                'head_commit': payload.get('head_commit', {})
            })
        
        # GitLab webhook format
        elif 'project' in payload:
            project = payload['project']
            repo_data.update({
                'name': project.get('name'),
                'full_name': project.get('path_with_namespace'),
                'clone_url': project.get('http_url'),
                'ssh_url': project.get('ssh_url'),
                'default_branch': project.get('default_branch', 'main'),
                'ref': payload.get('ref', '').replace('refs/heads/', ''),
                'commits': payload.get('commits', []),
                'pusher': payload.get('user_name', 'unknown'),
                'head_commit': payload.get('commits', [{}])[-1] if payload.get('commits') else {}
            })
        
        # Bitbucket webhook format
        elif 'repository' in payload and 'push' in payload:
            repository = payload['repository']
            push = payload['push']
            changes = push.get('changes', [{}])[0] if push.get('changes') else {}
            
            repo_data.update({
                'name': repository.get('name'),
                'full_name': repository.get('full_name'),
                'clone_url': repository.get('links', {}).get('clone', [{}])[0].get('href'),
                'default_branch': repository.get('mainbranch', {}).get('name', 'main'),
                'ref': changes.get('new', {}).get('name', ''),
                'commits': changes.get('commits', []),
                'pusher': payload.get('actor', {}).get('display_name', 'unknown')
            })
        
        return repo_data
        
    except Exception as e:
        current_app.logger.warning(f"Failed to extract repository info: {e}")
        return {}

@webhook_bp.route('/<string:pipeline_token>', methods=['POST'])
def trigger_pipeline(pipeline_token):
    """Trigger pipeline execution via webhook"""
    start_time = datetime.utcnow()
    
    try:
        # Validate pipeline token
        if not pipeline_token or len(pipeline_token) < 10:
            current_app.logger.warning(f"Invalid pipeline token format: {pipeline_token[:10]}...")
            return jsonify({'error': 'Invalid pipeline token'}), 400
        
        # Find pipeline by trigger token
        pipeline = Pipeline.query.filter_by(trigger_token=pipeline_token).first()
        
        if not pipeline:
            current_app.logger.warning(f"Pipeline not found for token: {pipeline_token[:10]}...")
            return jsonify({'error': 'Pipeline not found'}), 404
        
        if not pipeline.is_active:
            current_app.logger.warning(f"Pipeline {pipeline.name} is not active")
            return jsonify({'error': 'Pipeline is not active'}), 403
        
        # Get active configuration
        config = pipeline.get_active_configuration()
        if not config:
            current_app.logger.error(f"No active configuration for pipeline {pipeline.name}")
            return jsonify({'error': 'No active pipeline configuration'}), 400
        
        # Parse webhook payload
        try:
            payload = request.get_json(force=True) if request.get_data() else {}
        except Exception as e:
            current_app.logger.warning(f"Failed to parse webhook payload: {e}")
            payload = {}
        
        # Extract repository information for logging
        repo_info = extract_repository_info(payload)
        
        # Verify signature for GitHub webhooks (optional)
        github_signature = request.headers.get('X-Hub-Signature-256')
        if github_signature:
            # This would require the pipeline to have a webhook secret configured
            # For now, we'll just log it
            current_app.logger.info(f"GitHub webhook signature present for pipeline {pipeline.name}")
        
        # Log webhook details
        current_app.logger.info(f"Webhook received for pipeline '{pipeline.name}' from {request.remote_addr}")
        if repo_info.get('full_name'):
            current_app.logger.info(f"Repository: {repo_info['full_name']}, Branch: {repo_info.get('ref', 'unknown')}")
        if repo_info.get('pusher'):
            current_app.logger.info(f"Pushed by: {repo_info['pusher']}")
        
        # Create execution record
        execution_metadata = {
            'webhook_source': request.headers.get('User-Agent', 'unknown'),
            'remote_addr': request.remote_addr,
            'repository_info': repo_info,
            'payload_size': len(request.get_data()),
            'headers': dict(request.headers),
            'received_at': start_time.isoformat()
        }
        
        execution = PipelineExecution(
            pipeline_id=pipeline.id,
            configuration_id=config.id,
            trigger_method='webhook',
            status='pending',
            started_at=start_time,
            execution_metadata=execution_metadata
        )
        
        db.session.add(execution)
        db.session.commit()
        
        current_app.logger.info(f"Created execution {execution.id} for pipeline {pipeline.name}")
        
        # Execute pipeline asynchronously
        try:
            executor = PipelineExecutor()
            thread = executor.execute_async(execution.id)
            
            response_data = {
                'message': 'Pipeline triggered successfully',
                'execution_id': execution.id,
                'pipeline_name': pipeline.name,
                'pipeline_id': pipeline.id,
                'status': 'pending',
                'triggered_at': start_time.isoformat()
            }
            
            # Include repository info in response if available
            if repo_info:
                response_data['repository'] = {
                    'name': repo_info.get('name'),
                    'branch': repo_info.get('ref'),
                    'pusher': repo_info.get('pusher')
                }
            
            current_app.logger.info(f"Pipeline {pipeline.name} execution started successfully")
            return jsonify(response_data), 200
            
        except Exception as e:
            # Update execution status on error
            execution.status = 'failed'
            execution.error_message = f"Failed to start execution: {str(e)}"
            execution.completed_at = datetime.utcnow()
            db.session.commit()
            
            current_app.logger.error(f"Failed to start execution for pipeline {pipeline.name}: {e}")
            
            return jsonify({
                'error': 'Failed to start pipeline execution',
                'execution_id': execution.id,
                'details': str(e)
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Webhook error: {e}")
        return jsonify({
            'error': 'Internal server error',
            'details': str(e) if current_app.debug else 'Please contact administrator'
        }), 500

@webhook_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring"""
    try:
        # Check database connectivity
        db.session.execute('SELECT 1')
        
        # Check if Docker is available
        executor = PipelineExecutor()
        docker_status = "available" if executor.docker_available else "unavailable"
        
        # Basic system info
        import psutil
        system_info = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent
        }
        
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'service': 'CI/CD Pipeline Platform',
            'version': '1.0.0',
            'database': 'connected',
            'docker': docker_status,
            'system': system_info
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'timestamp': datetime.utcnow().isoformat(),
            'error': str(e)
        }), 503

@webhook_bp.route('/stats', methods=['GET'])
def webhook_stats():
    """Get webhook and execution statistics"""
    try:
        # Get basic statistics
        from sqlalchemy import func
        from models.pipeline import PipelineExecution
        
        stats = db.session.query(
            func.count(PipelineExecution.id).label('total_executions'),
            func.count(PipelineExecution.id).filter(
                PipelineExecution.status == 'success'
            ).label('successful_executions'),
            func.count(PipelineExecution.id).filter(
                PipelineExecution.status == 'failed'
            ).label('failed_executions'),
            func.count(PipelineExecution.id).filter(
                PipelineExecution.trigger_method == 'webhook'
            ).label('webhook_executions'),
            func.count(PipelineExecution.id).filter(
                PipelineExecution.trigger_method == 'manual'
            ).label('manual_executions')
        ).first()
        
        # Recent activity (last 24 hours)
        from datetime import timedelta
        recent_cutoff = datetime.utcnow() - timedelta(hours=24)
        
        recent_stats = db.session.query(
            func.count(PipelineExecution.id).label('recent_executions')
        ).filter(
            PipelineExecution.started_at >= recent_cutoff
        ).first()
        
        return jsonify({
            'total_executions': stats.total_executions,
            'successful_executions': stats.successful_executions,
            'failed_executions': stats.failed_executions,
            'webhook_executions': stats.webhook_executions,
            'manual_executions': stats.manual_executions,
            'recent_executions_24h': recent_stats.recent_executions,
            'success_rate': round(
                (stats.successful_executions / max(stats.total_executions, 1)) * 100, 2
            ),
            'timestamp': datetime.utcnow().isoformat()
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Stats query failed: {e}")
        return jsonify({'error': 'Failed to retrieve statistics'}), 500

@webhook_bp.errorhandler(404)
def webhook_not_found(e):
    """Custom 404 handler for webhook endpoints"""
    return jsonify({
        'error': 'Webhook endpoint not found',
        'message': 'Please check your pipeline token and try again'
    }), 404

@webhook_bp.errorhandler(500)
def webhook_server_error(e):
    """Custom 500 handler for webhook endpoints"""
    current_app.logger.error(f"Webhook server error: {e}")
    return jsonify({
        'error': 'Internal server error',
        'message': 'Please try again later or contact administrator'
    }), 500