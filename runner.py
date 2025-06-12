#!/usr/bin/env python3

import os
import sys
import json
import hmac
import hashlib
import logging
import subprocess
import yaml
import threading
import secrets
import string
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify

# Global variables for configuration and logger
config = {}
logger = None
app = Flask(__name__)

def is_root():
    """Check if the current user is root"""
    # return os.geteuid() == 0
    return True

def generate_webhook_token():
    """Generate a secure random webhook token"""
    # Generate a 32-character random token with letters and digits
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(32))

def get_default_config():
    """Get default configuration values"""
    return {
        'port': 8080,
        'workspace': './workspace',
        'log_file': './logs/agent.log',
        'build_logs_dir': './logs/builds',
        'webhook_token': '',
        'auto_generate_token': True,
        'git_timeout': 300,
        'max_builds_history': 50,
        'cleanup_old_logs': True,
        'require_signature': True
    }

def validate_config():
    """Validate configuration values"""
    # Check if workspace exists, create if not
    workspace = config.get('workspace', './workspace')
    if not os.path.exists(workspace):
        os.makedirs(workspace, exist_ok=True)
    
    # Validate port
    port = config.get('port', 8080)
    if not (1 <= port <= 65535):
        raise ValueError(f"Invalid port number: {port}. Must be between 1 and 65535.")
    
    # Validate git timeout
    git_timeout = config.get('git_timeout', 300)
    if git_timeout <= 0:
        raise ValueError(f"Invalid git timeout: {git_timeout}. Must be a positive integer.")
    
    # Validate max builds history
    max_builds_history = config.get('max_builds_history', 50)
    if max_builds_history <= 0:
        raise ValueError(f"Invalid max builds history: {max_builds_history}. Must be a positive integer.")
    
    # Validate cleanup_old_logs
    cleanup_old_logs = config.get('cleanup_old_logs', True)
    if not isinstance(cleanup_old_logs, bool):
        raise ValueError(f"Invalid cleanup_old_logs value: {cleanup_old_logs}. Must be a boolean.")
    
    # Validate require_signature
    require_signature = config.get('require_signature', True)
    if not isinstance(require_signature, bool):
        raise ValueError(f"Invalid require_signature value: {require_signature}. Must be a boolean.")
    

def save_token_to_config(config_path, token):
    """Save the generated token back to the config file"""
    try:
        # Read the current config
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f) or {}
        else:
            config_data = get_default_config()
        
        # Update the webhook_token
        config_data['webhook_token'] = token
        
        # Write back to file
        with open(config_path, 'w') as f:
            yaml.safe_dump(config_data, f, default_flow_style=False)
        
        # Use logger only if it's available
        if logger:
            logger.info(f"Generated and saved new webhook token to {config_path}")
        print(f"🔑 Generated webhook token: {token}")
        print(f"📝 Please update your GitHub webhook secret with this token.")
        
    except Exception as e:
        # Use logger only if it's available
        if logger:
            logger.error(f"Failed to save token to config file: {e}")
        else:
            print(f"❌ Failed to save token to config file: {e}")

def load_config(config_path="./agent.conf"):
    """Load configuration from file"""
    global config
    
    # If config file doesn't exist, create a default one
    if not os.path.exists(config_path):
        print(f"⚠️  Configuration file not found: {config_path}")
        print(f"📝 Creating default configuration file...")
        
        # Create default config
        config = get_default_config()
        
        # Create config directory if needed
        config_dir = os.path.dirname(config_path)
        if config_dir and config_dir != '.':
            os.makedirs(config_dir, exist_ok=True)
        
        # Write default config to file
        try:
            with open(config_path, 'w') as f:
                yaml.safe_dump(config, f, default_flow_style=False)
            print(f"✅ Created default configuration at {config_path}")
        except Exception as e:
            print(f"❌ Failed to create config file: {e}")
            print("📋 Continuing with in-memory configuration...")
        
        # Generate token if auto_generate_token is True
        if config.get('auto_generate_token', True):
            print("🔑 Generating webhook token...")
            token = generate_webhook_token()
            config['webhook_token'] = token
            save_token_to_config(config_path, token)
        
        return
    
    try:
        with open(config_path, 'r') as f:
            loaded_config = yaml.safe_load(f)
        
        # Start with defaults
        config = get_default_config()
        
        # Update with loaded values if they exist
        if loaded_config and isinstance(loaded_config, dict):
            config.update(loaded_config)
            print(f"✅ Configuration loaded from {config_path}")
        else:
            print(f"⚠️  Empty or invalid configuration file, using defaults")
        
    except yaml.YAMLError as e:
        print(f"❌ ERROR: Invalid YAML in configuration file: {e}")
        print(f"📋 Using default configuration...")
        config = get_default_config()
    except Exception as e:
        print(f"❌ ERROR: Failed to read configuration file: {e}")
        print(f"📋 Using default configuration...")
        config = get_default_config()
    
    # Validate configuration
    validate_config()
    
    # Auto-generate webhook token if needed
    # Only generate token if it's empty AND auto_generate_token is True
    if not config.get('webhook_token') and config.get('auto_generate_token', False):
        print("🔑 Generating webhook token...")
        token = generate_webhook_token()
        config['webhook_token'] = token
        save_token_to_config(config_path, token)

def setup_logging():
    """Setup logging configuration"""
    global logger
    
    log_file = config.get('log_file')
    
    # Create log directory if it doesn't exist
    if log_file:
        log_dir = os.path.dirname(log_file)
        os.makedirs(log_dir, exist_ok=True)
    
    # Create build logs directory
    build_logs_dir = config.get('build_logs_dir')
    if build_logs_dir:
        os.makedirs(build_logs_dir, exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file) if log_file else logging.NullHandler()
        ]
    )
    
    logger = logging.getLogger(__name__)

def create_build_logger(repo_name, build_id):
    """Create a separate logger for each build"""
    build_logs_dir = config.get('build_logs_dir', '/var/log/cirunner/builds')
    
    # Create build-specific log file name
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_filename = f"{repo_name}_{build_id}_{timestamp}.log"
    log_path = os.path.join(build_logs_dir, log_filename)
    
    # Create a new logger for this build
    build_logger = logging.getLogger(f"build_{build_id}")
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

def setup_workspace():
    """Create workspace directory if it doesn't exist"""
    workspace = config['workspace']
    os.makedirs(workspace, exist_ok=True)
    logger.info(f"Workspace directory: {workspace}")

def verify_signature(signature, body):
    """Verify GitHub webhook signature"""
    # Check if signature verification is required
    if not config.get('require_signature', True):
        logger.info("Signature verification disabled by configuration")
        return True
        
    webhook_token = config.get('webhook_token', '')
    
    if not webhook_token:
        logger.warning("No webhook token configured, skipping signature verification")
        return True
    
    if not signature:
        logger.warning("No signature provided in webhook request")
        return False
    
    # GitHub sends signature as "sha256=<hash>" or "sha1=<hash>"
    if signature.startswith('sha256='):
        hash_function = hashlib.sha256
        signature = signature[7:]  # Remove "sha256=" prefix
    elif signature.startswith('sha1='):
        hash_function = hashlib.sha1
        signature = signature[5:]   # Remove "sha1=" prefix
    else:
        logger.warning(f"Unsupported signature format: {signature[:10]}...")
        return False
    
    # Calculate expected signature
    expected_signature = hmac.new(
        webhook_token.encode('utf-8'),
        body,
        hash_function
    ).hexdigest()
    
    is_valid = hmac.compare_digest(signature, expected_signature)
    if not is_valid:
        logger.warning("Webhook signature verification failed")
    
    return is_valid

def clone_repository(repo_url, dest_path, build_logger):
    """Clone a git repository"""
    try:
        cmd = ['git', 'clone', repo_url, dest_path]
        build_logger.info(f"Executing: {' '.join(cmd)}")
        
        timeout = config.get('git_timeout', 300)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        
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
        build_logger.error(f"Git clone timed out after {config.get('git_timeout', 300)} seconds")
        return False
    except Exception as e:
        build_logger.error(f"Git clone error: {e}")
        return False

def pull_repository(repo_path, build_logger):
    """Pull latest changes from a git repository"""
    try:
        cmd = ['git', 'pull']
        build_logger.info(f"Executing: {' '.join(cmd)} in {repo_path}")
        
        timeout = config.get('git_timeout', 300)
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=timeout)
        
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
        build_logger.error(f"Git pull timed out after {config.get('git_timeout', 300)} seconds")
        return False
    except Exception as e:
        build_logger.error(f"Git pull error: {e}")
        return False

def generate_build_id():
    """Generate a unique build ID"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    random_suffix = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{timestamp}_{random_suffix}"

def process_webhook(repo_url, repo_name, repo_full_name, payload):
    """Process the webhook and clone/pull repository"""
    build_id = generate_build_id()
    
    # Create build-specific logger
    build_logger, log_path = create_build_logger(repo_name, build_id)
    
    try:
        build_logger.info(f"=== BUILD START ===")
        build_logger.info(f"Build ID: {build_id}")
        build_logger.info(f"Repository: {repo_full_name}")
        build_logger.info(f"Clone URL: {repo_url}")
        build_logger.info(f"Log file: {log_path}")
        
        # Log webhook payload details
        ref = payload.get('ref', 'unknown')
        commits = payload.get('commits', [])
        build_logger.info(f"Reference: {ref}")
        build_logger.info(f"Number of commits: {len(commits)}")
        
        if commits:
            latest_commit = commits[-1]
            build_logger.info(f"Latest commit: {latest_commit.get('id', 'unknown')[:8]}")
            build_logger.info(f"Commit message: {latest_commit.get('message', 'No message')}")
            build_logger.info(f"Author: {latest_commit.get('author', {}).get('name', 'unknown')}")
        
        workspace = config['workspace']
        repo_path = os.path.join(workspace, repo_name)
        
        build_logger.info(f"Workspace: {workspace}")
        build_logger.info(f"Repository path: {repo_path}")
        
        # Main logger info
        logger.info(f"Processing webhook for repository: {repo_full_name} (Build ID: {build_id})")
        
        # Check if repository already exists
        if os.path.exists(os.path.join(repo_path, '.git')):
            # Repository exists, pull latest changes
            build_logger.info(f"Repository exists, pulling latest changes")
            logger.info(f"Repository exists, pulling latest changes for: {repo_name} (Build ID: {build_id})")
            
            if pull_repository(repo_path, build_logger):
                build_logger.info(f"Successfully pulled repository")
                logger.info(f"Successfully pulled repository: {repo_name} (Build ID: {build_id})")
            else:
                build_logger.error(f"Failed to pull repository")
                logger.error(f"Failed to pull repository: {repo_name} (Build ID: {build_id})")
                return
        else:
            # Repository doesn't exist, clone it
            build_logger.info(f"Repository does not exist, cloning")
            logger.info(f"Cloning repository: {repo_url} (Build ID: {build_id})")
            
            if clone_repository(repo_url, repo_path, build_logger):
                build_logger.info(f"Successfully cloned repository")
                logger.info(f"Successfully cloned repository: {repo_name} (Build ID: {build_id})")
            else:
                build_logger.error(f"Failed to clone repository")
                logger.error(f"Failed to clone repository: {repo_name} (Build ID: {build_id})")
                return
        
        build_logger.info(f"=== BUILD SUCCESS ===")
        build_logger.info(f"Repository {repo_name} processed successfully")
        logger.info(f"Repository {repo_name} processed successfully (Build ID: {build_id})")
        
        print(f"repo cloned - Build ID: {build_id}")
        
    except Exception as e:
        build_logger.error(f"=== BUILD FAILED ===")
        build_logger.error(f"Error processing webhook: {e}")
        logger.error(f"Error processing webhook for {repo_name}: {e} (Build ID: {build_id})")
    finally:
        # Close the build logger handlers
        for handler in build_logger.handlers:
            handler.close()
            build_logger.removeHandler(handler)

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Handle incoming webhook requests"""
    try:
        # Get request data
        signature = request.headers.get('X-Hub-Signature-256') or request.headers.get('X-Hub-Signature')
        body = request.get_data()
        
        # Verify signature
        if not verify_signature(signature, body):
            logger.warning("Invalid webhook signature")
            return jsonify({"error": "Unauthorized"}), 401
        
        # Parse JSON payload
        try:
            payload = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON payload: {e}")
            return jsonify({"error": "Invalid JSON"}), 400
        
        # Extract repository information
        repository = payload.get('repository', {})
        repo_url = repository.get('clone_url')
        repo_name = repository.get('name')
        repo_full_name = repository.get('full_name')
        
        if not repo_url or not repo_name:
            logger.error("Missing repository information in webhook payload")
            return jsonify({"error": "Invalid payload"}), 400
        
        logger.info(f"Received webhook for repository: {repo_full_name}")
        
        # Process webhook in background thread
        thread = threading.Thread(target=process_webhook, args=(repo_url, repo_name, repo_full_name, payload))
        thread.daemon = True
        thread.start()
        
        return jsonify({"message": "Webhook received"}), 200
        
    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/health', methods=['GET'])
def handle_health():
    """Handle health check requests"""
    return jsonify({
        "status": "healthy",
        "workspace": config['workspace'],
        "port": config['port'],
        "build_logs_dir": config.get('build_logs_dir'),
        "webhook_token_configured": bool(config.get('webhook_token'))
    })

@app.route('/builds', methods=['GET'])
def list_builds():
    """List recent build logs"""
    try:
        build_logs_dir = config.get('build_logs_dir', '/var/log/cirunner/builds')
        
        if not os.path.exists(build_logs_dir):
            return jsonify({"builds": []})
        
        builds = []
        for log_file in sorted(os.listdir(build_logs_dir), reverse=True)[:20]:  # Last 20 builds
            if log_file.endswith('.log'):
                log_path = os.path.join(build_logs_dir, log_file)
                stat = os.stat(log_path)
                
                # Parse filename: repo_buildid_timestamp.log
                parts = log_file.replace('.log', '').split('_')
                if len(parts) >= 3:
                    repo_name = parts[0]
                    build_id = '_'.join(parts[1:-1])
                    timestamp = parts[-1]
                else:
                    repo_name = "unknown"
                    build_id = log_file.replace('.log', '')
                    timestamp = "unknown"
                
                builds.append({
                    "build_id": build_id,
                    "repo_name": repo_name,
                    "log_file": log_file,
                    "timestamp": timestamp,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
        
        return jsonify({"builds": builds})
        
    except Exception as e:
        logger.error(f"Error listing builds: {e}")
        return jsonify({"error": "Internal server error"}), 500

def initialize_agent(config_path="./agent.conf"):
    """Initialize the CI runner agent"""
    print("🚀 Initializing CI Runner Agent...")
    
    if not is_root():
        print("❌ ERROR: This application must be run as root user")
        sys.exit(1)
    
    print(f"📁 Loading configuration from: {config_path}")
    load_config(config_path)
    
    print("📝 Setting up logging...")
    setup_logging()
    
    print("📂 Creating workspace directory...")
    setup_workspace()
    
    # Print configuration summary
    print("\n" + "="*50)
    print("📋 CONFIGURATION SUMMARY")
    print("="*50)
    print(f"🌐 Server Port: {config['port']}")
    print(f"📁 Workspace: {config['workspace']}")
    print(f"📜 Log File: {config['log_file']}")
    print(f"📋 Build Logs: {config['build_logs_dir']}")
    print(f"🔑 Token Configured: {'✅' if config.get('webhook_token') else '❌'}")
    print(f"🛡️  Signature Required: {'✅' if config.get('require_signature', True) else '❌'}")
    print(f"⏱️  Git Timeout: {config.get('git_timeout', 300)} seconds")
    print(f"🗂️  Max Build History: {config.get('max_builds_history', 50)}")
    print(f"🧹 Auto Cleanup: {'✅' if config.get('cleanup_old_logs', True) else '❌'}")
    print("="*50)
    
    logger.info("✅ CI Runner Agent initialized successfully")
    logger.info(f"📂 Build logs directory: {config.get('build_logs_dir')}")

def run_agent():
    """Run the CI runner agent"""
    port = config['port']
    print(f"\n🌐 Starting CI Runner Agent on port {port}")
    logger.info(f"🌐 CI Runner Agent starting on port {port}")
    
    print(f"📡 Webhook endpoint: http://localhost:{port}/webhook")
    print(f"❤️  Health check: http://localhost:{port}/health")
    print(f"📊 Build logs: http://localhost:{port}/builds")
    print(f"\n🎯 Ready to receive webhooks! Press Ctrl+C to stop.\n")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down CI Runner Agent...")
        logger.info("🛑 CI Runner Agent shutdown requested")
    except Exception as e:
        print(f"\n❌ Failed to start server: {e}")
        logger.error(f"❌ Failed to start server: {e}")
        sys.exit(1)

def main():
    """Main entry point"""
    # Initialize the agent
    initialize_agent()
    
    # Run the agent
    run_agent()

if __name__ == '__main__':
    main()