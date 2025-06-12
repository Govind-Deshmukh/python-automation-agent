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
from datetime import datetime
from pathlib import Path
from models.pipeline import PipelineExecution, PipelineConfiguration, Pipeline
from extensions import db
from flask import current_app

class PipelineExecutor:
    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            current_app.logger.error(f"Failed to initialize Docker client: {e}")
            self.docker_client = None
        
        # Configuration from app config or defaults
        self.workspace = current_app.config.get('WORKSPACE_DIR', './workspace')
        self.build_logs_dir = current_app.config.get('BUILD_LOGS_DIR', './logs/builds')
        self.git_timeout = current_app.config.get('GIT_TIMEOUT', 300)
        
        # Ensure directories exist
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.build_logs_dir, exist_ok=True)
    
    def create_build_logger(self, pipeline_name, execution_id):
        """Create a separate logger for each build execution"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f"{pipeline_name}_{execution_id}_{timestamp}.log"
        log_path = os.path.join(self.build_logs_dir, log_filename)
        
        # Create a new logger for this build
        build_logger = logging.getLogger(f"build_{execution_id}")
        build_logger.setLevel(logging.INFO)
        
        # Remove any existing handlers to avoid duplication
        for handler in build_logger.handlers[:]:
            build_logger.removeHandler(handler)
        
        # Create file handler for this build
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        build_logger.addHandler(file_handler)
        build_logger.propagate = False  # Prevent propagation to root logger
        
        return build_logger, log_path
    
    def clone_repository(self, repo_url, dest_path, build_logger, branch='main'):
        """Clone a git repository"""
        try:
            # Remove existing directory if it exists
            if os.path.exists(dest_path):
                import shutil
                shutil.rmtree(dest_path)
            
            cmd = ['git', 'clone', '-b', branch, repo_url, dest_path]
            build_logger.info(f"Executing: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=self.git_timeout
            )
            
            if result.returncode == 0:
                build_logger.info(f"Git clone successful")
                build_logger.info(f"Git clone stdout: {result.stdout}")
                if result.stderr:
                    build_logger.info(f"Git clone stderr: {result.stderr}")
                return True
            else:
                build_logger.error(f"Git clone failed with return code: {result.returncode}")
                build_logger.error(f"Git clone stderr: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            build_logger.error(f"Git clone timed out after {self.git_timeout} seconds")
            return False
        except Exception as e:
            build_logger.error(f"Git clone error: {e}")
            return False
    
    def pull_repository(self, repo_path, build_logger):
        """Pull latest changes from a git repository"""
        try:
            cmd = ['git', 'pull']
            build_logger.info(f"Executing: {' '.join(cmd)} in {repo_path}")
            
            result = subprocess.run(
                cmd, 
                cwd=repo_path, 
                capture_output=True, 
                text=True, 
                timeout=self.git_timeout
            )
            
            if result.returncode == 0:
                build_logger.info(f"Git pull successful")
                build_logger.info(f"Git pull stdout: {result.stdout}")
                if result.stderr:
                    build_logger.info(f"Git pull stderr: {result.stderr}")
                return True
            else:
                build_logger.error(f"Git pull failed with return code: {result.returncode}")
                build_logger.error(f"Git pull stderr: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            build_logger.error(f"Git pull timed out after {self.git_timeout} seconds")
            return False
        except Exception as e:
            build_logger.error(f"Git pull error: {e}")
            return False
    
    def fetch_yaml_from_repo(self, repo_url, branch, file_path, build_logger):
        """Fetch YAML file from Git repository"""
        try:
            # Create a temporary directory for cloning
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                if self.clone_repository(repo_url, temp_dir, build_logger, branch):
                    yaml_file_path = os.path.join(temp_dir, file_path)
                    if os.path.exists(yaml_file_path):
                        with open(yaml_file_path, 'r') as f:
                            return f.read()
                    else:
                        raise FileNotFoundError(f"YAML file not found: {file_path}")
                else:
                    raise Exception("Failed to clone repository")
        except Exception as e:
            build_logger.error(f"Failed to fetch YAML from repo: {e}")
            raise
    
    def parse_pipeline_yaml(self, yaml_content):
        """Parse and validate pipeline YAML"""
        try:
            config = yaml.safe_load(yaml_content)
            
            # Basic validation
            if not isinstance(config, dict):
                raise ValueError("YAML must be a dictionary")
            
            if 'tasks' not in config:
                raise ValueError("YAML must contain 'tasks' field")
            
            if not isinstance(config['tasks'], list):
                raise ValueError("'tasks' must be a list")
            
            # Set defaults
            config.setdefault('env_image', 'ubuntu:24.10')
            config.setdefault('variables', {})
            
            return config
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {str(e)}")
    
    def execute_task_in_docker(self, task, env_image, environment_vars, build_logger, repo_path=None):
        """Execute a single task in a Docker container"""
        if not self.docker_client:
            raise Exception("Docker client not available")
        
        try:
            task_name = task.get('name', 'unnamed_task')
            command = task.get('command', '')
            
            if not command:
                raise ValueError(f"Task '{task_name}' has no command")
            
            build_logger.info(f"Executing task: {task_name}")
            build_logger.info(f"Command: {command}")
            
            # Prepare volumes if we have a repo path
            volumes = {}
            working_dir = '/workspace'
            if repo_path and os.path.exists(repo_path):
                volumes[repo_path] = {'bind': '/workspace', 'mode': 'rw'}
                build_logger.info(f"Mounting {repo_path} to /workspace")
            
            # Run the container
            container = self.docker_client.containers.run(
                env_image,
                command=f'/bin/bash -c "{command}"',
                environment=environment_vars,
                volumes=volumes,
                working_dir=working_dir,
                detach=True,
                remove=True,
                stdout=True,
                stderr=True
            )
            
            # Wait for completion and get logs
            result = container.wait()
            logs = container.logs(stdout=True, stderr=True).decode('utf-8')
            
            build_logger.info(f"Task '{task_name}' completed with exit code: {result['StatusCode']}")
            build_logger.info(f"Task output:\n{logs}")
            
            if result['StatusCode'] != 0:
                raise Exception(f"Task '{task_name}' failed with exit code {result['StatusCode']}")
            
            return True, logs
            
        except Exception as e:
            build_logger.error(f"Failed to execute task '{task_name}': {e}")
            return False, str(e)
    
    def execute_pipeline(self, execution_id):
        """Execute a pipeline"""
        with current_app.app_context():
            execution = PipelineExecution.query.get(execution_id)
            if not execution:
                current_app.logger.error(f"Execution {execution_id} not found")
                return False
            
            pipeline = execution.pipeline
            config = PipelineConfiguration.query.get(execution.configuration_id)
            
            # Create build logger
            build_logger, log_path = self.create_build_logger(
                pipeline.name.replace(' ', '_'), 
                execution_id
            )
            
            try:
                # Update execution status
                execution.status = 'running'
                execution.started_at = datetime.utcnow()
                db.session.commit()
                
                build_logger.info(f"=== PIPELINE EXECUTION START ===")
                build_logger.info(f"Pipeline: {pipeline.name}")
                build_logger.info(f"Execution ID: {execution_id}")
                build_logger.info(f"Log file: {log_path}")
                
                # Get YAML content
                yaml_content = config.yaml_content
                repo_path = None
                
                if config.yaml_source == 'repo':
                    build_logger.info(f"Fetching YAML from repository: {config.repo_url}")
                    yaml_content = self.fetch_yaml_from_repo(
                        config.repo_url,
                        config.repo_branch,
                        config.yaml_file_path,
                        build_logger
                    )
                    
                    # Also clone the repo for task execution
                    repo_name = pipeline.name.replace(' ', '_').replace('/', '_')
                    repo_path = os.path.join(self.workspace, f"{repo_name}_{execution_id}")
                    
                    build_logger.info(f"Cloning repository for task execution")
                    if not self.clone_repository(config.repo_url, repo_path, build_logger, config.repo_branch):
                        raise Exception("Failed to clone repository for task execution")
                
                # Parse pipeline configuration
                build_logger.info("Parsing pipeline YAML configuration")
                pipeline_config = self.parse_pipeline_yaml(yaml_content)
                
                # Prepare environment variables
                environment_vars = pipeline_config.get('variables', {})
                env_image = pipeline_config.get('env_image', 'ubuntu:24.10')
                
                build_logger.info(f"Using Docker image: {env_image}")
                build_logger.info(f"Environment variables: {environment_vars}")
                
                # Execute tasks
                all_logs = []
                tasks = pipeline_config.get('tasks', [])
                
                build_logger.info(f"Executing {len(tasks)} tasks")
                
                for i, task in enumerate(tasks, 1):
                    build_logger.info(f"--- Task {i}/{len(tasks)} ---")
                    
                    success, task_logs = self.execute_task_in_docker(
                        task, env_image, environment_vars, build_logger, repo_path
                    )
                    
                    all_logs.append(f"=== Task {i}: {task.get('name', 'unnamed')} ===")
                    all_logs.append(task_logs)
                    all_logs.append("")
                    
                    if not success:
                        raise Exception(f"Task {i} failed: {task_logs}")
                
                # Success
                execution.status = 'success'
                execution.logs = '\n'.join(all_logs)
                build_logger.info(f"=== PIPELINE EXECUTION SUCCESS ===")
                current_app.logger.info(f"Pipeline {pipeline.name} executed successfully (ID: {execution_id})")
                
            except Exception as e:
                execution.status = 'failed'
                execution.error_message = str(e)
                build_logger.error(f"=== PIPELINE EXECUTION FAILED ===")
                build_logger.error(f"Error: {e}")
                current_app.logger.error(f"Pipeline {pipeline.name} execution failed (ID: {execution_id}): {e}")
            
            finally:
                execution.completed_at = datetime.utcnow()
                db.session.commit()
                
                # Clean up repo path if it was created
                if repo_path and os.path.exists(repo_path):
                    try:
                        import shutil
                        shutil.rmtree(repo_path)
                        build_logger.info(f"Cleaned up temporary repository: {repo_path}")
                    except Exception as e:
                        build_logger.warning(f"Failed to clean up {repo_path}: {e}")
                
                # Close the build logger handlers
                for handler in build_logger.handlers:
                    handler.close()
                    build_logger.removeHandler(handler)
            
            return execution.status == 'success'
    
    def execute_async(self, execution_id):
        """Execute pipeline asynchronously in a background thread"""
        thread = threading.Thread(target=self.execute_pipeline, args=(execution_id,))
        thread.daemon = True
        thread.start()
        current_app.logger.info(f"Started async execution for pipeline execution {execution_id}")
        return thread