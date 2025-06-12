from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models.user import User
from models.pipeline import Pipeline, PipelinePermission
from extensions import db
from sqlalchemy import or_

user_mgmt_bp = Blueprint('user_mgmt', __name__)

def login_required(f):
    """Decorator to require login for web routes"""
    import functools
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('web.login'))
        return f(*args, **kwargs)
    return decorated_function

@user_mgmt_bp.route('/pipelines/<int:pipeline_id>/permissions')
@login_required
def manage_pipeline_permissions(pipeline_id):
    """Manage permissions for a specific pipeline"""
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=session['user_id']).first()
    
    if not pipeline:
        flash('Pipeline not found or you do not have permission to manage it', 'error')
        return redirect(url_for('web.dashboard'))
    
    # Get current permissions
    permissions = PipelinePermission.query.filter_by(pipeline_id=pipeline_id).all()
    
    # Get all users for potential adding
    all_users = User.query.filter(User.id != session['user_id']).all()
    
    # Get users who already have permissions
    users_with_permissions = [p.user_id for p in permissions]
    available_users = [u for u in all_users if u.id not in users_with_permissions]
    
    return render_template('manage_permissions.html', 
                         pipeline=pipeline, 
                         permissions=permissions,
                         available_users=available_users)

@user_mgmt_bp.route('/pipelines/<int:pipeline_id>/permissions/add', methods=['POST'])
@login_required
def add_pipeline_permission(pipeline_id):
    """Add a user permission to a pipeline"""
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=session['user_id']).first()
    
    if not pipeline:
        flash('Pipeline not found or you do not have permission to manage it', 'error')
        return redirect(url_for('web.dashboard'))
    
    user_id = request.form.get('user_id')
    permission_level = request.form.get('permission_level')
    
    if not user_id or not permission_level:
        flash('User and permission level are required', 'error')
        return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))
    
    # Check if user exists
    user = User.query.get(user_id)
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))
    
    # Check if permission already exists
    existing_permission = PipelinePermission.query.filter_by(
        pipeline_id=pipeline_id, 
        user_id=user_id
    ).first()
    
    if existing_permission:
        flash(f'User {user.username} already has permissions for this pipeline', 'warning')
        return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))
    
    try:
        permission = PipelinePermission(
            pipeline_id=pipeline_id,
            user_id=user_id,
            permission_level=permission_level,
            granted_by_user_id=session['user_id']
        )
        db.session.add(permission)
        db.session.commit()
        
        flash(f'Successfully granted {permission_level} permission to {user.username}', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Failed to add permission', 'error')
    
    return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))

@user_mgmt_bp.route('/pipelines/<int:pipeline_id>/permissions/<int:permission_id>/update', methods=['POST'])
@login_required
def update_pipeline_permission(pipeline_id, permission_id):
    """Update a user's permission level"""
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=session['user_id']).first()
    
    if not pipeline:
        flash('Pipeline not found or you do not have permission to manage it', 'error')
        return redirect(url_for('web.dashboard'))
    
    permission = PipelinePermission.query.filter_by(
        id=permission_id, 
        pipeline_id=pipeline_id
    ).first()
    
    if not permission:
        flash('Permission not found', 'error')
        return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))
    
    new_permission_level = request.form.get('permission_level')
    
    if not new_permission_level:
        flash('Permission level is required', 'error')
        return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))
    
    try:
        permission.permission_level = new_permission_level
        db.session.commit()
        
        flash(f'Successfully updated {permission.user.username}\'s permission to {new_permission_level}', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Failed to update permission', 'error')
    
    return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))

@user_mgmt_bp.route('/pipelines/<int:pipeline_id>/permissions/<int:permission_id>/remove', methods=['POST'])
@login_required
def remove_pipeline_permission(pipeline_id, permission_id):
    """Remove a user's permission from a pipeline"""
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=session['user_id']).first()
    
    if not pipeline:
        flash('Pipeline not found or you do not have permission to manage it', 'error')
        return redirect(url_for('web.dashboard'))
    
    permission = PipelinePermission.query.filter_by(
        id=permission_id, 
        pipeline_id=pipeline_id
    ).first()
    
    if not permission:
        flash('Permission not found', 'error')
        return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))
    
    try:
        username = permission.user.username
        db.session.delete(permission)
        db.session.commit()
        
        flash(f'Successfully removed {username}\'s access to this pipeline', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Failed to remove permission', 'error')
    
    return redirect(url_for('user_mgmt.manage_pipeline_permissions', pipeline_id=pipeline_id))

def get_user_accessible_pipelines(user_id):
    """Get all pipelines a user can access (owned + permitted)"""
    # Pipelines owned by user
    owned_pipelines = Pipeline.query.filter_by(owner_id=user_id)
    
    # Pipelines where user has permissions
    permitted_pipelines = Pipeline.query.join(PipelinePermission).filter(
        PipelinePermission.user_id == user_id
    )
    
    # Combine and return
    all_pipelines = owned_pipelines.union(permitted_pipelines).all()
    return all_pipelines

def check_pipeline_permission(user_id, pipeline_id, required_permission='reader'):
    """Check if user has required permission for pipeline"""
    # Check if user owns the pipeline
    pipeline = Pipeline.query.filter_by(id=pipeline_id, owner_id=user_id).first()
    if pipeline:
        return True, 'owner'
    
    # Check if user has explicit permission
    permission = PipelinePermission.query.filter_by(
        pipeline_id=pipeline_id,
        user_id=user_id
    ).first()
    
    if not permission:
        return False, None
    
    # Permission level hierarchy
    permission_levels = ['reader', 'developer', 'maintainer', 'owner']
    
    user_level_index = permission_levels.index(permission.permission_level)
    required_level_index = permission_levels.index(required_permission)
    
    return user_level_index >= required_level_index, permission.permission_level