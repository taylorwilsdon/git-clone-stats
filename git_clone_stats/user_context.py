#!/usr/bin/env python3
"""
User context management for multi-user and legacy single-user support.

Provides a clean interface for user-aware operations and backward compatibility.
"""

import os
from typing import Dict, List, Optional

from .app import DatabaseManager, GitHubStatsTracker
from .auth import is_oauth_configured


class UserContext:
    """Manages user context for both OAuth and legacy authentication modes."""
    
    def __init__(self, db_manager: DatabaseManager, user_session: Optional[Dict] = None):
        """
        Initialize user context.
        
        Args:
            db_manager: Database manager instance
            user_session: User session data from OAuth (None for legacy mode)
        """
        self.db_manager = db_manager
        self.user_session = user_session
        self.is_oauth_mode = is_oauth_configured()
        
        # In OAuth mode, we need user_session. In legacy mode, we use env vars
        if self.is_oauth_mode and user_session:
            self.user_id = user_session.get('user_id')
            self.github_username = user_session.get('github_username')
            self.github_token = self._get_user_token()
        else:
            # Legacy mode - use environment variables
            self.user_id = None
            self.github_username = os.environ.get('GITHUB_USERNAME')
            self.github_token = os.environ.get('GITHUB_TOKEN')
    
    def _get_user_token(self) -> Optional[str]:
        """Get GitHub token for the current user."""
        if not self.user_id:
            return None
        
        user_data = self.db_manager.get_user_by_id(self.user_id)
        return user_data.get('github_token') if user_data else None
    
    def is_authenticated(self) -> bool:
        """Check if user is properly authenticated."""
        if self.is_oauth_mode:
            return bool(self.user_session and self.github_token)
        else:
            # Legacy mode - check environment variables
            return bool(self.github_username and self.github_token)
    
    def get_user_info(self) -> Dict:
        """Get user information for display."""
        if self.is_oauth_mode and self.user_session:
            return {
                'username': self.user_session.get('github_username'),
                'name': self.user_session.get('github_name'),
                'email': self.user_session.get('github_email'),
                'avatar_url': self.user_session.get('github_avatar_url'),
                'auth_mode': 'oauth'
            }
        else:
            return {
                'username': self.github_username,
                'name': None,
                'email': None,
                'avatar_url': None,
                'auth_mode': 'legacy'
            }
    
    def get_tracked_repos(self) -> List[Dict[str, str]]:
        """Get tracked repositories for the current user context."""
        return self.db_manager.get_tracked_repos(self.user_id)
    
    def add_tracked_repo(self, repo_name: str, owner_type: str = 'user') -> bool:
        """Add a repository to tracking for the current user context."""
        return self.db_manager.add_tracked_repo(repo_name, owner_type, self.user_id)
    
    def remove_tracked_repo(self, repo_name: str) -> bool:
        """Remove a repository from tracking for the current user context."""
        return self.db_manager.remove_tracked_repo(repo_name, self.user_id)
    
    def create_stats_tracker(self) -> GitHubStatsTracker:
        """Create a GitHubStatsTracker for the current user context."""
        if not self.is_authenticated():
            raise ValueError("User not authenticated")
        
        # Get tracked repos for this user context
        tracked_repos = self.get_tracked_repos()
        repo_names = [repo['repo_name'] for repo in tracked_repos]
        
        return GitHubStatsTracker(
            github_token=self.github_token,
            github_username=self.github_username,
            repos=repo_names,
            db_manager=self.db_manager
        )
    
    def sync_repositories(self) -> tuple[bool, str]:
        """Sync repositories for the current user context."""
        try:
            if not self.is_authenticated():
                return False, "User not authenticated"
            
            tracker = self.create_stats_tracker()
            tracker.update_all_repositories()
            return True, "Sync completed successfully"
            
        except Exception as e:
            return False, f"Sync failed: {str(e)}"


class UserContextManager:
    """Factory for creating user contexts."""
    
    @staticmethod
    def from_request_headers(db_manager: DatabaseManager, headers: Dict[str, str]) -> UserContext:
        """
        Create user context from HTTP request headers.
        
        Args:
            db_manager: Database manager instance
            headers: HTTP request headers
            
        Returns:
            UserContext instance
        """
        if is_oauth_configured():
            # OAuth mode - try to extract session
            from .auth import get_oauth_handler
            oauth = get_oauth_handler()
            user_session = oauth.validate_session(headers)
            return UserContext(db_manager, user_session)
        else:
            # Legacy mode - no session needed
            return UserContext(db_manager, None)
    
    @staticmethod
    def for_legacy_mode(db_manager: DatabaseManager) -> UserContext:
        """
        Create user context for legacy single-user mode.
        
        Args:
            db_manager: Database manager instance
            
        Returns:
            UserContext instance for legacy mode
        """
        return UserContext(db_manager, None)
    
    @staticmethod
    def for_oauth_user(db_manager: DatabaseManager, user_session: Dict) -> UserContext:
        """
        Create user context for OAuth authenticated user.
        
        Args:
            db_manager: Database manager instance
            user_session: User session data from OAuth
            
        Returns:
            UserContext instance for OAuth user
        """
        return UserContext(db_manager, user_session)


def requires_auth(func):
    """Decorator to require authentication for user context operations."""
    def wrapper(self, *args, **kwargs):
        if not self.is_authenticated():
            if self.is_oauth_mode:
                raise ValueError("OAuth authentication required")
            else:
                raise ValueError("GitHub credentials not configured. Set GITHUB_TOKEN and GITHUB_USERNAME environment variables.")
        return func(self, *args, **kwargs)
    return wrapper