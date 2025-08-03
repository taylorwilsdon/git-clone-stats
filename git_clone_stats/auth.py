#!/usr/bin/env python3
"""
GitHub OAuth authentication handlers.

Implements the GitHub OAuth 2.0 flow for seamless user authentication.
"""

import json
import os
import secrets
import urllib.parse
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

from .session import get_session_manager


class GitHubOAuth:
    """GitHub OAuth 2.0 authentication handler."""
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """
        Initialize GitHub OAuth handler.
        
        Args:
            client_id: GitHub OAuth app client ID (from env if None)
            client_secret: GitHub OAuth app client secret (from env if None)
        """
        self.client_id = client_id or os.environ.get('GITHUB_CLIENT_ID')
        self.client_secret = client_secret or os.environ.get('GITHUB_CLIENT_SECRET')
        
        if not self.client_id or not self.client_secret:
            raise ValueError("GitHub OAuth credentials not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables.")
        
        self.session_manager = get_session_manager()
        
        # GitHub OAuth endpoints
        self.authorize_url = "https://github.com/login/oauth/authorize"
        self.token_url = "https://github.com/login/oauth/access_token"
        self.user_api_url = "https://api.github.com/user"
    
    def get_authorization_url(self, redirect_uri: str, state: Optional[str] = None) -> str:
        """
        Generate GitHub OAuth authorization URL.
        
        Args:
            redirect_uri: URL to redirect to after authorization
            state: Optional state parameter for CSRF protection
            
        Returns:
            GitHub authorization URL
        """
        if state is None:
            state = secrets.token_urlsafe(32)
        
        params = {
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'scope': 'repo',  # Required for accessing repository statistics
            'state': state
        }
        
        return f"{self.authorize_url}?{urlencode(params)}"
    
    def exchange_code_for_token(self, code: str, redirect_uri: str) -> Optional[str]:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from GitHub
            redirect_uri: Redirect URI used in authorization
            
        Returns:
            Access token if successful, None otherwise
        """
        try:
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
                'redirect_uri': redirect_uri
            }
            
            headers = {
                'Accept': 'application/json',
                'User-Agent': 'git-clone-stats/1.0'
            }
            
            response = requests.post(self.token_url, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            token_data = response.json()
            
            if 'access_token' in token_data:
                return token_data['access_token']
            else:
                error = token_data.get('error_description', 'Unknown error')
                raise ValueError(f"Token exchange failed: {error}")
                
        except Exception as e:
            print(f"Error exchanging code for token: {e}")
            return None
    
    def get_user_info(self, access_token: str) -> Optional[Dict]:
        """
        Get user information from GitHub API.
        
        Args:
            access_token: GitHub access token
            
        Returns:
            User information dictionary if successful, None otherwise
        """
        try:
            headers = {
                'Authorization': f'token {access_token}',
                'Accept': 'application/json',
                'User-Agent': 'git-clone-stats/1.0'
            }
            
            response = requests.get(self.user_api_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            print(f"Error getting user info: {e}")
            return None
    
    def create_user_session(self, user_data: Dict) -> str:
        """
        Create a secure session for the authenticated user.
        
        Args:
            user_data: User information from GitHub API
            
        Returns:
            Encrypted session cookie value
        """
        session_data = {
            'user_id': user_data['id'],
            'github_id': user_data['id'],
            'github_username': user_data['login'],
            'github_name': user_data.get('name'),
            'github_email': user_data.get('email'),
            'github_avatar_url': user_data.get('avatar_url')
        }
        
        return self.session_manager.create_session(session_data)
    
    def validate_session(self, request_headers: Dict[str, str]) -> Optional[Dict]:
        """
        Validate user session from request headers.
        
        Args:
            request_headers: HTTP request headers (case-insensitive)
            
        Returns:
            User session data if valid, None otherwise
        """
        return self.session_manager.extract_session_from_headers(request_headers)
    
    def create_logout_response(self) -> str:
        """
        Create Set-Cookie header to clear the session.
        
        Returns:
            Set-Cookie header value for logout
        """
        return self.session_manager.create_logout_cookie_header()


class AuthenticationRequired(Exception):
    """Exception raised when authentication is required but not provided."""
    pass


def get_oauth_handler() -> GitHubOAuth:
    """Get a configured GitHub OAuth handler."""
    return GitHubOAuth()


def require_authentication(request_headers: Dict[str, str]) -> Dict:
    """
    Decorator function to require authentication for endpoints.
    
    Args:
        request_headers: HTTP request headers
        
    Returns:
        User session data if authenticated
        
    Raises:
        AuthenticationRequired: If user is not authenticated
    """
    oauth = get_oauth_handler()
    user_session = oauth.validate_session(request_headers)
    
    if not user_session:
        raise AuthenticationRequired("Authentication required")
    
    return user_session


def is_oauth_configured() -> bool:
    """Check if GitHub OAuth is properly configured."""
    return bool(
        os.environ.get('GITHUB_CLIENT_ID') and 
        os.environ.get('GITHUB_CLIENT_SECRET')
    )