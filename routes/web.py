from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models.user import User
from models.pipeline import Pipeline, PipelineConfiguration, PipelineExecution
from services.pipeline_executor import PipelineExecutor
from routes.user_management import get_user_accessible_pipelines, check_pipeline_permission
from extensions import db
from datetime import datetime
import functools

def login_required(f):
    """Decorator to require login for web routes"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('web.login'))
        return f(*args, **kwargs)
    return decorated_function

web_bp = Blueprint('web', __name__)

@web_bp.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('web.dashboard'))
    return redirect(url_for('web.login'))

@web_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Please enter both username and password', 'error')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('web.dashboard'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@web_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Validation
        if not all([username, email, password, confirm_password]):
            flash('All fields are required', 'error')
            return render_template('signup.html')
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('signup.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long', 'error')
            return render_template('signup.html')
        
        # Check if user exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('signup.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists', 'error')
            return render_template('signup.html')
        
        # Create new user
        try:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('web.login'))
        except Exception as e:
            db.session.rollback()
            flash('An error occurred while creating your account', 'error')
    
    return render_template('signup.html')

@web_bp.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    user = User.query.get(user_id)
    
    # Get pipelines user can access (owned + permitted)
    pipelines = get_user_accessible_pipelines(user_id)
    
    # Get recent executions for accessible pipelines
    pipeline_ids = [p.id for p in pipelines]
    recent_executions = []
    if pipeline_ids:
        recent_executions = PipelineExecution.query.filter(
            PipelineExecution.pipeline_id.in_(pipeline_ids)
        ).order_by(PipelineExecution.started_at.desc()).limit(5).all()
    
    return render_template('dashboard.html', 
                         user=user, 
                         pipelines=pipelines, 
                         recent_executions=recent_executions)

@web_bp.route('/pipelines/new', methods=['GET', 'POST'])
@login_required
def create_pipeline():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        yaml_source = request.form.get('yaml_source')
        
        if not name:
            flash('Pipeline name is required', 'error')
            return render_template('create_pipeline.html')
        
        try:
            # Create pipeline
            pipeline = Pipeline(
                owner_id=session['user_id'],
                name=name,
                description=description or ''
            )
            db.session.add(pipeline)
            db.session.flush()
            
            # Create configuration
            config = PipelineConfiguration(
                pipeline_id=pipeline.id,
                yaml_source=yaml_source
            )
            
            if yaml_source == 'editor':
                config.yaml_content = request.form.get('yaml_content', '')
            else:  # repo
                config.repo_url = request.form.get('repo_url', '')
                config.repo_branch = request.form.get('repo_branch', 'main')
                config.yaml_file_path = request.form.get('yaml_file_path', 'pipeline.yml')
            
            db.session.add(config)
            db.session.commit()
            
            flash(f'Pipeline "{name}" created successfully!', 'success')
            return redirect(url_for('web.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash('An error occurred while creating the pipeline', 'error')
    
    return render_template('create_pipeline.html')

@web_bp.route('/pipelines/<int:pipeline_id>')
@login_required
def view_pipeline(pipeline_id):
    # Check if user has permission to view this pipeline
    has_permission, permission_level = check_pipeline_permission(session['user_id'], pipeline_id, 'reader')
    
    if not has_permission:
        flash('Pipeline not found or you do not have permission to view it', 'error')
        return redirect(url_for('web.dashboard'))
    
    pipeline = Pipeline.query.get(pipeline_id)
    config = pipeline.get_active_configuration()
    executions = PipelineExecution.query.filter_by(pipeline_id=pipeline_id).order_by(
        PipelineExecution.started_at.desc()
    ).limit(10).all()
    
    # Check if user is owner (for showing manage permissions link)
    is_owner = pipeline.owner_id == session['user_id']
    
    return render_template('view_pipeline.html', 
                         pipeline=pipeline, 
                         config=config, 
                         executions=executions,
                         is_owner=is_owner,
                         permission_level=permission_level)

@web_bp.route('/pipelines/<int:pipeline_id>/trigger', methods=['POST'])
@login_required
def trigger_pipeline_manual(pipeline_id):
    # Check if user has permission to trigger this pipeline
    has_permission, permission_level = check_pipeline_permission(session['user_id'], pipeline_id, 'developer')
    
    if not has_permission:
        flash('Pipeline not found or you do not have permission to trigger it', 'error')
        return redirect(url_for('web.dashboard'))
    
    pipeline = Pipeline.query.get(pipeline_id)
    config = pipeline.get_active_configuration()
    
    if not config:
        flash('No active configuration found', 'error')
        return redirect(url_for('web.view_pipeline', pipeline_id=pipeline_id))
    
    try:
        execution = PipelineExecution(
            pipeline_id=pipeline.id,
            configuration_id=config.id,
            triggered_by_user_id=session['user_id'],
            trigger_method='manual',
            status='pending',
            started_at=datetime.utcnow()
        )
        db.session.add(execution)
        db.session.commit()
        
        # Execute pipeline asynchronously
        executor = PipelineExecutor()
        executor.execute_async(execution.id)
        
        flash('Pipeline triggered successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to trigger pipeline: {str(e)}', 'error')
    
    return redirect(url_for('web.view_pipeline', pipeline_id=pipeline_id))

@web_bp.route('/pipelines/<int:pipeline_id>/executions/<int:execution_id>')
@login_required
def view_execution(pipeline_id, execution_id):
    """View detailed execution logs"""
    # Check if user has permission to view this pipeline
    has_permission, permission_level = check_pipeline_permission(session['user_id'], pipeline_id, 'reader')
    
    if not has_permission:
        flash('Pipeline not found or you do not have permission to view it', 'error')
        return redirect(url_for('web.dashboard'))
    
    pipeline = Pipeline.query.get(pipeline_id)
    execution = PipelineExecution.query.filter_by(id=execution_id, pipeline_id=pipeline_id).first()
    
    if not execution:
        flash('Execution not found', 'error')
        return redirect(url_for('web.view_pipeline', pipeline_id=pipeline_id))
    
    return render_template('view_execution.html', pipeline=pipeline, execution=execution)

@web_bp.route('/api/executions/<int:execution_id>/status')
@login_required
def get_execution_status(execution_id):
    """API endpoint to get execution status (for polling)"""
    execution = PipelineExecution.query.get(execution_id)
    
    if not execution:
        return jsonify({'error': 'Execution not found'}), 404
    
    # Check if user has permission to view this pipeline
    has_permission, _ = check_pipeline_permission(session['user_id'], execution.pipeline_id, 'reader')
    
    if not has_permission:
        return jsonify({'error': 'Permission denied'}), 403
    
    return jsonify({
        'id': execution.id,
        'status': execution.status,
        'started_at': execution.started_at.isoformat() if execution.started_at else None,
        'completed_at': execution.completed_at.isoformat() if execution.completed_at else None,
        'error_message': execution.error_message
    })

@web_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('web.login'))