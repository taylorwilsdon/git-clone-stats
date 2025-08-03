#!/usr/bin/env python3
"""
GitHub Repository Clone Statistics Web Server

A simple web server to expose the collected clone statistics via a JSON API.
"""

import http.server
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from http import HTTPStatus
from pathlib import Path

from .app import run_sync, DatabaseManager
from .db_factory import get_database_manager
from .server_db_adapter import get_database_adapter
from .auth import get_oauth_handler, is_oauth_configured, AuthenticationRequired
from .user_context import UserContextManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# DB_PATH is now managed by db_factory


class StatsRequestHandler(http.server.SimpleHTTPRequestHandler):
    """A custom request handler to serve clone statistics."""

    def __init__(self, *args, **kwargs):
        # Set the directory to serve static files from
        static_dir = Path(__file__).parent / "static"
        super().__init__(*args, directory=str(static_dir), **kwargs)

    def _send_json_response(self, data: dict, status: HTTPStatus = HTTPStatus.OK):
        """Send a JSON response with consistent headers."""
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

    def _send_json_error(self, message: str, status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR):
        """Send a JSON error response."""
        self._send_json_response({"success": False, "message": message}, status)

    def _generate_badge_svg(self, label: str, message: str, color: str) -> str:
        """Generate an SVG badge with the given parameters."""
        # Simple badge template with fixed dimensions
        label_width = len(label) * 7 + 10
        message_width = len(message) * 7 + 10
        total_width = label_width + message_width
        
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20">
        <linearGradient id="b" x2="0" y2="100%">
            <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
            <stop offset="1" stop-opacity=".1"/>
        </linearGradient>
        <mask id="a">
            <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
        </mask>
        <g mask="url(#a)">
            <rect width="{label_width}" height="20" fill="#555"/>
            <rect x="{label_width}" width="{message_width}" height="20" fill="{color}"/>
            <rect width="{total_width}" height="20" fill="url(#b)"/>
        </g>
        <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
            <text x="{label_width/2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
            <text x="{label_width/2}" y="14">{label}</text>
            <text x="{label_width + message_width/2}" y="15" fill="#010101" fill-opacity=".3">{message}</text>
            <text x="{label_width + message_width/2}" y="14">{message}</text>
        </g>
        </svg>'''
        return svg

    def send_favicon(self):
        """Serve the SVG favicon."""
        import os
        from pathlib import Path
        
        # Look for favicon.svg in the project root
        favicon_path = Path(__file__).parent.parent / "favicon.svg"
        
        try:
            if favicon_path.exists():
                with open(favicon_path, 'r', encoding='utf-8') as f:
                    svg_content = f.read()
                
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-type", "image/svg+xml")
                self.send_header("Content-Length", str(len(svg_content.encode('utf-8'))))
                self.end_headers()
                self.wfile.write(svg_content.encode('utf-8'))
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Favicon not found")
        except Exception as e:
            logger.error(f"Error serving favicon: {e}")
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Error serving favicon")

    def do_GET(self):
        """Handle GET requests."""
        # Parse the URL and query parameters
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query_params = urllib.parse.parse_qs(parsed_path.query)

        # OAuth endpoints
        if path == "/auth/login":
            self.handle_oauth_login()
        elif path == "/auth/callback":
            self.handle_oauth_callback(query_params)
        elif path == "/auth/logout":
            self.handle_oauth_logout()
        elif path == "/auth/status":
            self.handle_auth_status()
        # API endpoints
        elif path == "/api/stats":
            self.send_stats()
        elif path == "/api/sync":
            self.send_sync_response()
        elif path == "/api/tracked-repos":
            self.send_tracked_repos()
        elif path == "/api/repo/history":
            repo_name = query_params.get('repo', [None])[0]
            history_type = query_params.get('type', ['clones'])[0]
            days = int(query_params.get('days', [30])[0])
            if repo_name:
                self.send_repo_history(repo_name, history_type, days)
            else:
                self._send_json_error("Missing 'repo' parameter", HTTPStatus.BAD_REQUEST)
        elif path == "/api/export":
            self.send_database_export()
        elif match := re.match(r"/badge/(.+)/total\.svg", path):
            repo_name = match.group(1)
            self.send_badge(repo_name)
        elif path == "/" or path == "/index.html":
            super().do_GET()
        elif path == "/favicon.ico":
            self.send_favicon()
        elif path.startswith("/static/"):
            # Remove /static/ prefix for the static directory
            self.path = path[8:]  # Remove "/static/" prefix
            super().do_GET()
        else:
            # For all other paths, try to serve from static directory
            super().do_GET()

    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path

        if path == "/api/tracked-repos/add":
            self.add_tracked_repo()
        elif path == "/api/tracked-repos/remove":
            self.remove_tracked_repo()
        elif path == "/api/import":
            self.import_database()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def send_sync_response(self):
        """Run the sync and send a response."""
        logger.info("Sync requested from web UI.")
        
        try:
            # Get user context
            headers = dict(self.headers)
            db_manager = get_database_manager()
            
            with db_manager:
                db_manager.setup_database()
                user_context = UserContextManager.from_request_headers(db_manager, headers)
                
                if not user_context.is_authenticated():
                    self._send_json_error("Authentication required", HTTPStatus.UNAUTHORIZED)
                    return
                
                # Run sync for the authenticated user
                success, message = user_context.sync_repositories()
                status = HTTPStatus.OK if success else HTTPStatus.INTERNAL_SERVER_ERROR
                self._send_json_response({"success": success, "message": message}, status)
                
        except Exception as e:
            logger.error(f"Sync error: {e}")
            self._send_json_error("Sync failed", HTTPStatus.INTERNAL_SERVER_ERROR)

    def get_stats_for_repo(self, repo_name: str) -> dict:
        """Retrieve statistics for a single repository."""
        try:
            db_manager = get_database_manager()
            with db_manager:
                adapter = get_database_adapter(db_manager)
                stats = adapter.get_stats_for_repo(repo_name)
                return {
                    "total_clones": stats.get('total_clones', 0),
                    "total_unique_clones": stats.get('total_unique_clones', 0),
                    "total_views": stats.get('total_views', 0),
                    "total_unique_views": stats.get('total_unique_views', 0)
                }
        except Exception as e:
            logger.error(f"Database error for repo {repo_name}: {e}")
            return None

    def send_badge(self, repo_name: str):
        """Serve a dynamic SVG badge for the repo."""
        stats = self.get_stats_for_repo(repo_name)
        if stats is None:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to retrieve stats from the database.")
            return

        total_clones = stats.get('total_clones', 0)
        message = f"{total_clones:,}"  # Add comma formatting for large numbers
        label = "clones"
        color = "blue"

        # Generate SVG badge directly
        svg_content = self._generate_badge_svg(label, message, color)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", "image/svg+xml")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(svg_content.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(svg_content.encode('utf-8'))

    def send_stats(self):
        """Retrieve all statistics from the database."""
        try:
            db_manager = get_database_manager()
            with db_manager:
                adapter = get_database_adapter(db_manager)
                results = adapter.get_all_repos_summary()
                
                stats = {
                    "success": True,
                    "stats": results,
                    "github_username": os.environ.get('GITHUB_USERNAME', ''),
                    "github_org": os.environ.get('GITHUB_ORG', ''),
                    "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                }
                self._send_json_response(stats)
        except Exception as e:
            logger.error(f"Failed to retrieve stats: {e}")
            self._send_json_error(f"Database error: {str(e)}")

    def send_tracked_repos(self):
        """Send the list of tracked repositories."""
        try:
            # Get user context
            headers = dict(self.headers)
            db_manager = get_database_manager()
            
            with db_manager:
                db_manager.setup_database()
                user_context = UserContextManager.from_request_headers(db_manager, headers)
                
                if not user_context.is_authenticated():
                    self._send_json_error("Authentication required", HTTPStatus.UNAUTHORIZED)
                    return
                
                tracked_repos = user_context.get_tracked_repos()
                
            self._send_json_response({
                "success": True,
                "repositories": tracked_repos
            })
        except Exception as e:
            logger.error(f"Failed to retrieve tracked repos: {e}")
            self._send_json_error(f"Database error: {str(e)}")

    def add_tracked_repo(self):
        """Handle adding a new tracked repository."""
        try:
            # Get user context first
            headers = dict(self.headers)
            db_manager = get_database_manager()
            
            with db_manager:
                db_manager.setup_database()
                user_context = UserContextManager.from_request_headers(db_manager, headers)
                
                if not user_context.is_authenticated():
                    self._send_json_error("Authentication required", HTTPStatus.UNAUTHORIZED)
                    return
                
                # Parse request data
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                repo_name = data.get('repo_name')
                owner_type = data.get('owner_type', 'user')
                
                if not repo_name:
                    self._send_json_error("Missing 'repo_name' in request body", HTTPStatus.BAD_REQUEST)
                    return
                    
                # Validate owner_type
                if owner_type not in ['user', 'org']:
                    self._send_json_error("Invalid 'owner_type'. Must be 'user' or 'org'", HTTPStatus.BAD_REQUEST)
                    return

                # Add repo for the authenticated user
                success = user_context.add_tracked_repo(repo_name, owner_type)
                
                if success:
                    self._send_json_response({"success": True, "message": f"Added {repo_name} to tracked repositories"})
                    logger.info(f"Added {repo_name} to tracked repositories for user {user_context.github_username}")
                else:
                    self._send_json_error(f"Failed to add {repo_name} to tracked repositories")
                    
        except json.JSONDecodeError:
            self._send_json_error("Invalid JSON in request body", HTTPStatus.BAD_REQUEST)
        except Exception as e:
            logger.error(f"Failed to add tracked repo: {e}")
            self._send_json_error(f"Server error: {str(e)}")

    def remove_tracked_repo(self):
        """Handle removing a tracked repository."""
        try:
            # Get user context first
            headers = dict(self.headers)
            db_manager = get_database_manager()
            
            with db_manager:
                db_manager.setup_database()
                user_context = UserContextManager.from_request_headers(db_manager, headers)
                
                if not user_context.is_authenticated():
                    self._send_json_error("Authentication required", HTTPStatus.UNAUTHORIZED)
                    return
                
                # Parse request data
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                repo_name = data.get('repo_name')
                if not repo_name:
                    self._send_json_error("Missing 'repo_name' in request body", HTTPStatus.BAD_REQUEST)
                    return

                # Remove repo for the authenticated user
                success = user_context.remove_tracked_repo(repo_name)
                
                if success:
                    self._send_json_response({"success": True, "message": f"Removed {repo_name} from tracked repositories"})
                    logger.info(f"Removed {repo_name} from tracked repositories for user {user_context.github_username}")
                else:
                    self._send_json_error(f"Failed to remove {repo_name} from tracked repositories")
                    
        except json.JSONDecodeError:
            self._send_json_error("Invalid JSON in request body", HTTPStatus.BAD_REQUEST)
        except Exception as e:
            logger.error(f"Failed to remove tracked repo: {e}")
            self._send_json_error(f"Server error: {str(e)}")

    def send_repo_history(self, repo_name: str, history_type: str = 'clones', days: int = 30):
        """Send historical data for a specific repository."""
        try:
            db_manager = get_database_manager()
            with db_manager:
                adapter = get_database_adapter(db_manager)
                history_data = adapter.get_repo_history(repo_name, history_type, days)
                
            self._send_json_response({
                "success": True,
                "repo": repo_name,
                "type": history_type,
                "days": days,
                "data": history_data
            })
        except Exception as e:
            logger.error(f"Failed to retrieve history for {repo_name}: {e}")
            self._send_json_error(f"Database error: {str(e)}")

    def send_database_export(self):
        """Export database as JSON."""
        try:
            db_manager = get_database_manager()
            with db_manager:
                db_manager.setup_database()
                export_data = db_manager.export_database()
                
            self._send_json_response({
                "success": True,
                "export": export_data,
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
            })
        except Exception as e:
            logger.error(f"Failed to export database: {e}")
            self._send_json_error(f"Export error: {str(e)}")

    def import_database(self):
        """Import database from JSON."""
        try:
            content_length = int(self.headers['Content-Length'])
            if content_length > 10 * 1024 * 1024:  # 10MB limit
                self._send_json_error("Request body too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
                
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            import_data = data.get('import')
            replace_existing = data.get('replace_existing', False)
            
            if not import_data:
                self._send_json_error("Missing 'import' data in request body", HTTPStatus.BAD_REQUEST)
                return

            db_manager = get_database_manager()
            with db_manager:
                db_manager.setup_database()
                success = db_manager.import_database(import_data, replace_existing)
                
            if success:
                self._send_json_response({
                    "success": True,
                    "message": "Database imported successfully"
                })
                logger.info("Database imported successfully")
            else:
                self._send_json_error("Failed to import database")
        except json.JSONDecodeError:
            self._send_json_error("Invalid JSON in request body", HTTPStatus.BAD_REQUEST)
        except Exception as e:
            logger.error(f"Failed to import database: {e}")
            self._send_json_error(f"Import error: {str(e)}")

    # OAuth authentication endpoints
    
    def handle_oauth_login(self):
        """Handle OAuth login initiation."""
        if not is_oauth_configured():
            self._send_json_error("OAuth not configured", HTTPStatus.SERVICE_UNAVAILABLE)
            return
        
        try:
            oauth = get_oauth_handler()
            
            # Build redirect URI
            host = self.headers.get('Host', 'localhost:8080')
            protocol = 'https' if 'https' in self.headers.get('Referer', '') else 'http'
            redirect_uri = f"{protocol}://{host}/auth/callback"
            
            # Generate authorization URL
            auth_url = oauth.get_authorization_url(redirect_uri)
            
            # Redirect to GitHub
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", auth_url)
            self.end_headers()
            
        except Exception as e:
            logger.error(f"OAuth login error: {e}")
            self._send_json_error("OAuth login failed", HTTPStatus.INTERNAL_SERVER_ERROR)
    
    def handle_oauth_callback(self, query_params: dict):
        """Handle OAuth callback from GitHub."""
        if not is_oauth_configured():
            self._send_json_error("OAuth not configured", HTTPStatus.SERVICE_UNAVAILABLE)
            return
        
        try:
            # Extract authorization code
            code = query_params.get('code', [None])[0]
            if not code:
                error = query_params.get('error', ['unknown'])[0]
                self._send_json_error(f"OAuth authorization failed: {error}", HTTPStatus.BAD_REQUEST)
                return
            
            oauth = get_oauth_handler()
            
            # Build redirect URI (must match the one used in login)
            host = self.headers.get('Host', 'localhost:8080')
            protocol = 'https' if 'https' in self.headers.get('Referer', '') else 'http'
            redirect_uri = f"{protocol}://{host}/auth/callback"
            
            # Exchange code for token
            access_token = oauth.exchange_code_for_token(code, redirect_uri)
            if not access_token:
                self._send_json_error("Failed to exchange code for token", HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            
            # Get user info from GitHub
            user_info = oauth.get_user_info(access_token)
            if not user_info:
                self._send_json_error("Failed to get user information", HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            
            # Store user in database
            db_manager = get_database_manager()
            with db_manager:
                db_manager.setup_database()
                user_id = db_manager.create_or_update_user(user_info, access_token)
                
                # Update user_info with internal user_id for session
                user_info['user_id'] = user_id
            
            # Create session
            session_cookie = oauth.create_user_session(user_info)
            cookie_header = oauth.session_manager.create_cookie_header(session_cookie)
            
            # Redirect to main page with session cookie
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", cookie_header)
            self.end_headers()
            
        except Exception as e:
            logger.error(f"OAuth callback error: {e}")
            self._send_json_error("OAuth callback failed", HTTPStatus.INTERNAL_SERVER_ERROR)
    
    def handle_oauth_logout(self):
        """Handle user logout."""
        try:
            oauth = get_oauth_handler()
            logout_cookie = oauth.create_logout_response()
            
            # Redirect to main page with cleared session
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", logout_cookie)
            self.end_headers()
            
        except Exception as e:
            logger.error(f"Logout error: {e}")
            self._send_json_error("Logout failed", HTTPStatus.INTERNAL_SERVER_ERROR)
    
    def handle_auth_status(self):
        """Return current authentication status."""
        try:
            # Get request headers
            headers = dict(self.headers)
            
            # Create user context
            db_manager = get_database_manager()
            with db_manager:
                db_manager.setup_database()
                user_context = UserContextManager.from_request_headers(db_manager, headers)
                
                response_data = {
                    "authenticated": user_context.is_authenticated(),
                    "oauth_configured": is_oauth_configured(),
                    "user": user_context.get_user_info() if user_context.is_authenticated() else None
                }
                
                self._send_json_response(response_data)
                
        except Exception as e:
            logger.error(f"Auth status error: {e}")
            self._send_json_error("Failed to check auth status", HTTPStatus.INTERNAL_SERVER_ERROR)


class BackgroundSyncThread(threading.Thread):
    """A background thread to periodically sync stats."""
    
    def __init__(self, interval=3600):
        """
        Initialize the background sync thread.
        
        Args:
            interval: Sync interval in seconds (default: 1 hour)
        """
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True
        
    def run(self):
        """Run the background sync loop."""
        logger.info(f"Starting background sync thread (interval: {self.interval}s)")
        
        while self.running:
            try:
                # Wait for the interval
                time.sleep(self.interval)
                
                # Run sync
                logger.info("Running scheduled sync...")
                success, message = run_sync()
                if success:
                    logger.info("Scheduled sync completed successfully")
                else:
                    logger.error(f"Scheduled sync failed: {message}")
                    
            except Exception as e:
                logger.error(f"Error in background sync: {e}")
                
    def stop(self):
        """Stop the background sync thread."""
        self.running = False


def run_server(port: int = 8080, enable_background_sync: bool = True, sync_interval: int = 3600):
    """
    Run the statistics web server.
    
    Args:
        port: Port to listen on (default: 8080)
        enable_background_sync: Whether to enable background syncing (default: True)
        sync_interval: Sync interval in seconds (default: 3600 = 1 hour)
    """
    # Initialize database on startup
    db_path = os.environ.get('DATABASE_PATH', 'github_stats.db')
    logger.info(f"Initializing database at: {db_path}")
    
    # Get database manager and set up tables
    db_manager = get_database_manager()
    if hasattr(db_manager, 'setup_database'):
        with db_manager as db:
            db.setup_database()
            logger.info("Database tables initialized successfully")
    
    # Start background sync thread if enabled
    if enable_background_sync:
        sync_thread = BackgroundSyncThread(sync_interval)
        sync_thread.start()
    
    # Start the web server
    with http.server.HTTPServer(("", port), StatsRequestHandler) as httpd:
        logger.info(f"Starting server on port {port}")
        logger.info(f"Visit http://localhost:{port} to view statistics")
        if enable_background_sync:
            logger.info(f"Background sync enabled (interval: {sync_interval}s)")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server stopped by user")
            if enable_background_sync:
                sync_thread.stop()


if __name__ == "__main__":
    run_server()