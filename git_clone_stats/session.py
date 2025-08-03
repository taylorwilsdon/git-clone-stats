#!/usr/bin/env python3
"""
Lightweight session management for multi-user support.

Provides secure cookie-based sessions for GitHub OAuth authentication.
"""

import base64
import json
import os
import time
from typing import Dict, Optional
from cryptography.fernet import Fernet
from http.cookies import SimpleCookie


class SessionManager:
    """Simple, secure session management using encrypted cookies."""
    
    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize session manager with encryption key.
        
        Args:
            secret_key: Base64 encoded Fernet key. If None, uses SESSION_SECRET env var.
        """
        if secret_key is None:
            secret_key = os.environ.get('SESSION_SECRET')
            if not secret_key:
                # Generate a new key if none provided (for development)
                secret_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        
        try:
            # Ensure key is properly formatted for Fernet
            if isinstance(secret_key, str):
                secret_key = secret_key.encode()
            if len(secret_key) != 44:  # Base64 encoded 32-byte key length
                # Hash the provided key to get proper length
                import hashlib
                key_hash = hashlib.sha256(secret_key).digest()
                secret_key = base64.urlsafe_b64encode(key_hash)
            
            self.cipher = Fernet(secret_key)
        except Exception as e:
            raise ValueError(f"Invalid session secret key: {e}")
    
    def create_session(self, user_data: Dict) -> str:
        """
        Create an encrypted session cookie value.
        
        Args:
            user_data: Dictionary containing user session data
            
        Returns:
            Encrypted session cookie value
        """
        session_data = {
            'user': user_data,
            'created_at': int(time.time()),
            'expires_at': int(time.time()) + (7 * 24 * 60 * 60)  # 7 days
        }
        
        # Serialize and encrypt
        json_data = json.dumps(session_data).encode()
        encrypted_data = self.cipher.encrypt(json_data)
        return base64.urlsafe_b64encode(encrypted_data).decode()
    
    def get_session(self, session_cookie: str) -> Optional[Dict]:
        """
        Decrypt and validate a session cookie.
        
        Args:
            session_cookie: Encrypted session cookie value
            
        Returns:
            User session data if valid, None otherwise
        """
        try:
            # Decode and decrypt
            encrypted_data = base64.urlsafe_b64decode(session_cookie.encode())
            json_data = self.cipher.decrypt(encrypted_data)
            session_data = json.loads(json_data.decode())
            
            # Check expiration
            if session_data.get('expires_at', 0) < int(time.time()):
                return None
                
            return session_data.get('user')
            
        except Exception:
            return None
    
    def create_cookie_header(self, session_value: str, domain: Optional[str] = None) -> str:
        """
        Create a Set-Cookie header for the session.
        
        Args:
            session_value: Encrypted session value
            domain: Optional domain for the cookie
            
        Returns:
            Set-Cookie header value
        """
        cookie = SimpleCookie()
        cookie['session'] = session_value
        cookie['session']['httponly'] = True
        cookie['session']['secure'] = True  # Use HTTPS in production
        cookie['session']['samesite'] = 'Lax'
        cookie['session']['max-age'] = 7 * 24 * 60 * 60  # 7 days
        cookie['session']['path'] = '/'
        
        if domain:
            cookie['session']['domain'] = domain
        
        return cookie['session'].OutputString()
    
    def create_logout_cookie_header(self) -> str:
        """
        Create a Set-Cookie header to clear the session cookie.
        
        Returns:
            Set-Cookie header value that expires the session cookie
        """
        cookie = SimpleCookie()
        cookie['session'] = ''
        cookie['session']['expires'] = 'Thu, 01 Jan 1970 00:00:00 GMT'
        cookie['session']['path'] = '/'
        
        return cookie['session'].OutputString()
    
    def extract_session_from_headers(self, headers_dict: Dict[str, str]) -> Optional[Dict]:
        """
        Extract session data from HTTP headers.
        
        Args:
            headers_dict: Dictionary of HTTP headers (case-insensitive)
            
        Returns:
            User session data if valid session cookie found, None otherwise
        """
        # Find cookie header (case-insensitive)
        cookie_header = None
        for header_name, header_value in headers_dict.items():
            if header_name.lower() == 'cookie':
                cookie_header = header_value
                break
        
        if not cookie_header:
            return None
        
        # Parse cookies
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        
        # Extract session cookie
        if 'session' not in cookie:
            return None
        
        session_value = cookie['session'].value
        return self.get_session(session_value)


def get_session_manager() -> SessionManager:
    """Get a configured session manager instance."""
    return SessionManager()