from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models.user import User
from models.pipeline import Pipeline, PipelineConfiguration, PipelineExecution
from app import db, login_required

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
    pipelines = Pipeline.query.filter_by(owner_id=user_id).all()
    
    # Get recent executions
    recent_executions = PipelineExecution.query.join(Pipeline).filter(
        Pipeline.owner_id == user_id
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
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=session['user_id']).first()
    
    if not pipeline:
        flash('Pipeline not found', 'error')
        return redirect(url_for('web.dashboard'))
    
    config = pipeline.get_active_configuration()
    executions = PipelineExecution.query.filter_by(pipeline_id=pipeline_id).order_by(
        PipelineExecution.started_at.desc()
    ).limit(10).all()
    
    return render_template('view_pipeline.html', 
                         pipeline=pipeline, 
                         config=config, 
                         executions=executions)

@web_bp.route('/pipelines/<int:pipeline_id>/trigger', methods=['POST'])
@login_required
def trigger_pipeline_manual(pipeline_id):
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=session['user_id']).first()
    
    if not pipeline:
        flash('Pipeline not found', 'error')
        return redirect(url_for('web.dashboard'))
    
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
            status='pending'
        )
        db.session.add(execution)
        db.session.commit()
        
        flash('Pipeline triggered successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Failed to trigger pipeline', 'error')
    
    return redirect(url_for('web.view_pipeline', pipeline_id=pipeline_id))

@web_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('web.login'))
