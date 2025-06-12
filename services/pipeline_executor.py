import os
import sys
import json
import yaml
import docker
import logging
import subprocess
import threading
import secrets
import string
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from extensions import db
from flask import current_app

class PipelineExecutor:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Initialize Docker client
        try:
            self.docker_client = docker.from_env()
            # Test Docker connection
            self.docker_client.ping()
            self.docker_available = True
            self.logger.info("Docker client initialized successfully")
        except Exception as e:
            self.logger.warning(f"Docker not available: {e}")
            self.docker_client = None
            self.docker_available = False
        
        # Configuration
        if current_app:
            self.workspace = current_app.config.get('WORKSPACE_DIR', './workspace')
            self.build_logs_dir = current_app.config.get('BUILD_LOGS_DIR', './logs/builds')
            self.git_timeout = current_app.config.get('GIT_TIMEOUT', 300)
            self.docker_enabled = current_app.config.get('DOCKER_ENABLED', True)
            self.default_image = current_app.config.get('DEFAULT_DOCKER_IMAGE', 'ubuntu:24.10')
        else:
            # Fallback configuration
            self.workspace = './workspace'
            self.build_logs_dir = './logs/builds'
            self.git_timeout = 300
            self.docker_enabled = True
            self.default_image = 'ubuntu:24.10'
        
        # Ensure directories exist
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.build_logs_dir, exist_ok=True)
    
    def create_build_logger(self, pipeline_name, execution_id):
        """Create a dedicated logger for build execution"""
        # Sanitize pipeline name for filename
        safe_name = "".join(c for c in pipeline_name if c.isalnum() or c in ('-', '_')).strip()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f"{safe_name}_{execution_id}_{timestamp}.log"
        log_path = os.path.join(self.build_logs_dir, log_filename)
        
        # Create logger
        build_logger = logging.getLogger(f"build_{execution_id}")
        build_logger.setLevel(logging.INFO)
        
        # Clear any existing handlers
        for handler in build_logger.handlers[:]:
            build_logger.removeHandler(handler)
        
        # File handler
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        
        # Add handler
        build_logger.addHandler(file_handler)
        build_logger.propagate = False
        
        return build_logger, log_path
    
    def clone_repository(self, repo_url, dest_path, build_logger, branch='main'):
        """Clone Git repository with improved error handling"""
        try:
            # Clean destination if it exists
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            # Clone command
            cmd = ['git', 'clone', '--depth', '1', '-b', branch, repo_url, dest_path]
            build_logger.info(f"Cloning repository: {' '.join(cmd[:-1])} [DESTINATION]")
            
            # Execute with timeout
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.git_timeout,
                cwd=os.path.dirname(dest_path) or '.'
            )
            
            if result.returncode == 0:
                build_logger.info("Repository cloned successfully")
                if result.stdout:
                    build_logger.debug(f"Git stdout: {result.stdout}")
                if result.stderr:
                    build_logger.debug(f"Git stderr: {result.stderr}")
                return True
            else:
                build_logger.error(f"Git clone failed (exit code {result.returncode})")
                build_logger.error(f"Error: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            build_logger.error(f"Git clone timed out after {self.git_timeout} seconds")
            return False
        except FileNotFoundError:
            build_logger.error("Git command not found. Please install Git.")
            return False
        except Exception as e:
            build_logger.error(f"Unexpected error during git clone: {e}")
            return False
    
    def fetch_yaml_from_repo(self, repo_url, branch, file_path, build_logger):
        """Fetch YAML configuration from repository"""
        with tempfile.TemporaryDirectory() as temp_dir:
            if self.clone_repository(repo_url, temp_dir, build_logger, branch):
                yaml_file_path = os.path.join(temp_dir, file_path)
                if os.path.exists(yaml_file_path):
                    try:
                        with open(yaml_file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        build_logger.info(f"Successfully fetched YAML from {file_path}")
                        return content
                    except Exception as e:
                        raise Exception(f"Failed to read YAML file: {e}")
                else:
                    raise FileNotFoundError(f"YAML file not found: {file_path}")
            else:
                raise Exception("Failed to clone repository")
    
    def parse_pipeline_yaml(self, yaml_content):
        """Parse and validate pipeline YAML with comprehensive checks"""
        try:
            config = yaml.safe_load(yaml_content)
            
            if not isinstance(config, dict):
                raise ValueError("YAML must be a dictionary/object")
            
            # Validate required fields
            if 'tasks' not in config:
                raise ValueError("YAML must contain 'tasks' field")
            
            tasks = config['tasks']
            if not isinstance(tasks, list) or len(tasks) == 0:
                raise ValueError("'tasks' must be a non-empty list")
            
            # Validate each task
            for i, task in enumerate(tasks):
                if not isinstance(task, dict):
                    raise ValueError(f"Task {i+1} must be a dictionary")
                
                if 'command' not in task:
                    raise ValueError(f"Task {i+1} must have a 'command' field")
                
                if not task['command'].strip():
                    raise ValueError(f"Task {i+1} command cannot be empty")
            
            # Set defaults and validate
            config.setdefault('env_image', self.default_image)
            config.setdefault('variables', {})
            
            # Validate environment variables
            if not isinstance(config['variables'], dict):
                raise ValueError("'variables' must be a dictionary")
            
            # Validate Docker image name
            image = config['env_image']
            if not isinstance(image, str) or not image.strip():
                raise ValueError("'env_image' must be a non-empty string")
            
            return config
            
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML syntax: {str(e)}")
    
    def execute_task_in_docker(self, task, env_image, environment_vars, build_logger, repo_path=None):
        """Execute task in Docker container with enhanced error handling"""
        if not self.docker_available or not self.docker_enabled:
            return self.execute_task_locally(task, environment_vars, build_logger, repo_path)
        
        try:
            task_name = task.get('name', 'unnamed_task')
            command = task.get('command', '').strip()
            
            if not command:
                raise ValueError(f"Task '{task_name}' has no command")
            
            build_logger.info(f"Executing task: {task_name}")
            build_logger.info(f"Command: {command}")
            build_logger.info(f"Docker image: {env_image}")
            
            # Prepare volumes
            volumes = {}
            working_dir = '/workspace'
            
            if repo_path and os.path.exists(repo_path):
                volumes[os.path.abspath(repo_path)] = {'bind': '/workspace', 'mode': 'rw'}
                build_logger.info(f"Mounting {repo_path} to /workspace")
            
            # Prepare environment
            env_vars = dict(environment_vars)
            env_vars.update({
                'CI': 'true',
                'CICD_TASK_NAME': task_name,
                'CICD_EXECUTION_TIME': datetime.now().isoformat()
            })
            
            # Create and run container
            container = self.docker_client.containers.run(
                image=env_image,
                command=['sh', '-c', command],
                environment=env_vars,
                volumes=volumes,
                working_dir=working_dir,
                detach=True,
                remove=False,  # Don't auto-remove for log collection
                stdout=True,
                stderr=True,
                network_mode='bridge'
            )
            
            # Wait for completion
            result = container.wait()
            exit_code = result['StatusCode']
            
            # Get logs
            logs = container.logs(stdout=True, stderr=True).decode('utf-8', errors='replace')
            
            # Clean up container
            try:
                container.remove()
            except Exception as e:
                build_logger.warning(f"Failed to remove container: {e}")
            
            build_logger.info(f"Task '{task_name}' completed with exit code: {exit_code}")
            
            if logs:
                build_logger.info(f"Task output:\n{logs}")
            
            if exit_code != 0:
                raise Exception(f"Task '{task_name}' failed with exit code {exit_code}")
            
            return True, logs
            
        except docker.errors.ImageNotFound:
            error_msg = f"Docker image '{env_image}' not found"
            build_logger.error(error_msg)
            return False, error_msg
        except docker.errors.APIError as e:
            error_msg = f"Docker API error: {e}"
            build_logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Failed to execute task '{task_name}': {e}"
            build_logger.error(error_msg)
            return False, error_msg
    
    def execute_task_locally(self, task, environment_vars, build_logger, repo_path=None):
        """Fallback: Execute task locally without Docker"""
        try:
            task_name = task.get('name', 'unnamed_task')
            command = task.get('command', '').strip()
            
            build_logger.warning(f"Executing task '{task_name}' locally (Docker not available)")
            build_logger.info(f"Command: {command}")
            
            # Prepare environment
            env = os.environ.copy()
            env.update(environment_vars)
            env.update({
                'CI': 'true',
                'CICD_TASK_NAME': task_name,
                'CICD_EXECUTION_TIME': datetime.now().isoformat()
            })
            
            # Execute command
            result = subprocess.run(
                command,
                shell=True,
                cwd=repo_path or self.workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800  # 30 minutes max
            )
            
            output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
            
            build_logger.info(f"Task '{task_name}' completed with exit code: {result.returncode}")
            if output.strip():
                build_logger.info(f"Task output:\n{output}")
            
            if result.returncode != 0:
                raise Exception(f"Task '{task_name}' failed with exit code {result.returncode}")
            
            return True, output
            
        except subprocess.TimeoutExpired:
            error_msg = f"Task '{task_name}' timed out"
            build_logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Failed to execute task '{task_name}': {e}"
            build_logger.error(error_msg)
            return False, error_msg
    
    def execute_pipeline(self, execution_id):
        """Execute a complete pipeline with comprehensive error handling"""
        with current_app.app_context():
            from models.pipeline import PipelineExecution, PipelineConfiguration, Pipeline
            
            execution = PipelineExecution.query.get(execution_id)
            if not execution:
                self.logger.error(f"Execution {execution_id} not found")
                return False
            
            pipeline = execution.pipeline
            config = PipelineConfiguration.query.get(execution.configuration_id)
            
            # Create build logger
            build_logger, log_path = self.create_build_logger(
                pipeline.name, execution_id
            )
            
            repo_path = None
            all_logs = []
            
            try:
                # Update execution status
                execution.status = 'running'
                execution.started_at = datetime.utcnow()
                db.session.commit()
                
                build_logger.info("=== PIPELINE EXECUTION START ===")
                build_logger.info(f"Pipeline: {pipeline.name}")
                build_logger.info(f"Execution ID: {execution_id}")
                build_logger.info(f"Trigger: {execution.trigger_method}")
                build_logger.info(f"Docker available: {self.docker_available}")
                
                # Get YAML content
                yaml_content = config.yaml_content
                
                if config.yaml_source == 'repo':
                    build_logger.info(f"Fetching YAML from repository")
                    build_logger.info(f"Repository: {config.repo_url}")
                    build_logger.info(f"Branch: {config.repo_branch}")
                    build_logger.info(f"YAML path: {config.yaml_file_path}")
                    
                    yaml_content = self.fetch_yaml_from_repo(
                        config.repo_url,
                        config.repo_branch,
                        config.yaml_file_path,
                        build_logger
                    )
                    
                    # Clone repository for task execution
                    repo_name = "".join(c for c in pipeline.name if c.isalnum() or c in ('-', '_'))
                    repo_path = os.path.join(self.workspace, f"{repo_name}_{execution_id}")
                    
                    build_logger.info("Cloning repository for task execution")
                    if not self.clone_repository(config.repo_url, repo_path, build_logger, config.repo_branch):
                        raise Exception("Failed to clone repository for task execution")
                
                # Parse pipeline configuration
                build_logger.info("Parsing pipeline YAML configuration")
                pipeline_config = self.parse_pipeline_yaml(yaml_content)
                
                # Extract configuration
                environment_vars = pipeline_config.get('variables', {})
                env_image = pipeline_config.get('env_image', self.default_image)
                tasks = pipeline_config.get('tasks', [])
                
                build_logger.info(f"Environment image: {env_image}")
                build_logger.info(f"Environment variables: {list(environment_vars.keys())}")
                build_logger.info(f"Number of tasks: {len(tasks)}")
                
                # Execute tasks
                for i, task in enumerate(tasks, 1):
                    task_name = task.get('name', f'task_{i}')
                    build_logger.info(f"--- Executing Task {i}/{len(tasks)}: {task_name} ---")
                    
                    success, task_logs = self.execute_task_in_docker(
                        task, env_image, environment_vars, build_logger, repo_path
                    )
                    
                    # Collect logs
                    all_logs.append(f"=== Task {i}: {task_name} ===")
                    all_logs.append(task_logs or "No output")
                    all_logs.append("")
                    
                    if not success:
                        raise Exception(f"Task {i} ({task_name}) failed")
                    
                    build_logger.info(f"Task {i} completed successfully")
                
                # Pipeline completed successfully
                execution.status = 'success'
                execution.logs = '\n'.join(all_logs)
                build_logger.info("=== PIPELINE EXECUTION SUCCESS ===")
                
                if current_app:
                    current_app.logger.info(f"Pipeline '{pipeline.name}' executed successfully (ID: {execution_id})")
                
                return True
                
            except Exception as e:
                # Pipeline failed
                execution.status = 'failed'
                execution.error_message = str(e)
                
                # Include partial logs if available
                if all_logs:
                    execution.logs = '\n'.join(all_logs)
                
                build_logger.error("=== PIPELINE EXECUTION FAILED ===")
                build_logger.error(f"Error: {e}")
                
                if current_app:
                    current_app.logger.error(f"Pipeline '{pipeline.name}' execution failed (ID: {execution_id}): {e}")
                
                return False
            
            finally:
                # Always update completion time and save
                execution.completed_at = datetime.utcnow()
                
                try:
                    db.session.commit()
                except Exception as e:
                    build_logger.error(f"Failed to save execution results: {e}")
                    db.session.rollback()
                
                # Cleanup repository path
                if repo_path and os.path.exists(repo_path):
                    try:
                        shutil.rmtree(repo_path)
                        build_logger.info(f"Cleaned up temporary repository: {repo_path}")
                    except Exception as e:
                        build_logger.warning(f"Failed to clean up {repo_path}: {e}")
                
                # Close build logger
                for handler in build_logger.handlers[:]:
                    try:
                        handler.close()
                        build_logger.removeHandler(handler)
                    except Exception:
                        pass
    
    def execute_async(self, execution_id):
        """Execute pipeline asynchronously in background thread"""
        def run_execution():
            try:
                self.execute_pipeline(execution_id)
            except Exception as e:
                self.logger.error(f"Async execution failed for {execution_id}: {e}")
        
        thread = threading.Thread(target=run_execution, daemon=True)
        thread.start()
        
        if current_app:
            current_app.logger.info(f"Started async execution for pipeline execution {execution_id}")
        
        return thread
    
    def cancel_execution(self, execution_id):
        """Cancel a running execution (best effort)"""
        # This is a simplified implementation
        # In a production system, you'd want more sophisticated cancellation
        with current_app.app_context():
            from models.pipeline import PipelineExecution
            
            execution = PipelineExecution.query.get(execution_id)
            if execution and execution.status == 'running':
                execution.status = 'cancelled'
                execution.completed_at = datetime.utcnow()
                execution.error_message = "Execution cancelled by user"
                db.session.commit()
                
                self.logger.info(f"Marked execution {execution_id} as cancelled")
                return True
        
        return False
    
    def get_execution_logs(self, execution_id):
        """Get real-time logs for an execution"""
        try:
            # Find the log file for this execution
            log_pattern = f"*_{execution_id}_*.log"
            import glob
            
            log_files = glob.glob(os.path.join(self.build_logs_dir, log_pattern))
            
            if log_files:
                # Get the most recent log file
                log_file = max(log_files, key=os.path.getctime)
                
                with open(log_file, 'r', encoding='utf-8') as f:
                    return f.read()
            
            return "No logs available yet"
            
        except Exception as e:
            self.logger.error(f"Failed to read logs for execution {execution_id}: {e}")
            return f"Error reading logs: {e}"