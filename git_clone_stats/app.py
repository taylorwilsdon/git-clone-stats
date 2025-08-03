#!/usr/bin/env python3
"""
GitHub Repository Clone Statistics Tracker

A modern Python application for tracking and storing GitHub repository clone statistics.
This tool fetches clone data from the GitHub API and maintains historical records in a SQLite database.
"""

import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from .models import CloneRecord, ViewRecord


class DatabaseManager:
    """Handles all database operations for clone statistics."""

    def __init__(self, db_path: str):
        """
        Initialize the database manager.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self.conn = None
        self.logger = logging.getLogger(__name__)

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def setup_database(self):
        """Create the necessary tables if they don't exist."""
        try:
            with self.conn:
                # Create users table for OAuth authentication
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        github_id INTEGER UNIQUE NOT NULL,
                        github_username TEXT NOT NULL,
                        github_name TEXT,
                        github_email TEXT,
                        github_avatar_url TEXT,
                        github_token TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_login TEXT NOT NULL
                    )
                """)
                
                # Create legacy tables (backward compatibility)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS clone_history (
                        repo TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        count INTEGER NOT NULL,
                        uniques INTEGER NOT NULL,
                        user_id INTEGER,
                        PRIMARY KEY (repo, timestamp, COALESCE(user_id, 0)),
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                    )
                """)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS view_history (
                        repo TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        count INTEGER NOT NULL,
                        uniques INTEGER NOT NULL,
                        user_id INTEGER,
                        PRIMARY KEY (repo, timestamp, COALESCE(user_id, 0)),
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                    )
                """)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS tracked_repos (
                        repo_name TEXT NOT NULL,
                        added_at TEXT NOT NULL,
                        is_active INTEGER DEFAULT 1,
                        last_sync TEXT,
                        owner_type TEXT DEFAULT 'user',
                        user_id INTEGER,
                        PRIMARY KEY (repo_name, COALESCE(user_id, 0)),
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                    )
                """)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS repo_stars (
                        repo TEXT NOT NULL,
                        star_count INTEGER NOT NULL,
                        last_updated TEXT NOT NULL,
                        user_id INTEGER,
                        PRIMARY KEY (repo, COALESCE(user_id, 0)),
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                    )
                """)
                
                # Migration: Add user_id columns if they don't exist
                self._migrate_to_multiuser()
                
            self.logger.info("Database setup complete.")
        except sqlite3.Error as e:
            self.logger.error(f"Database setup failed: {e}")
            raise
    
    def _migrate_to_multiuser(self):
        """Migrate existing single-user data to multi-user schema."""
        try:
            # Check if user_id columns already exist
            cursor = self.conn.execute("PRAGMA table_info(clone_history)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'user_id' not in columns:
                # Add user_id columns to existing tables
                self.conn.execute("ALTER TABLE clone_history ADD COLUMN user_id INTEGER")
                self.conn.execute("ALTER TABLE view_history ADD COLUMN user_id INTEGER") 
                self.conn.execute("ALTER TABLE tracked_repos ADD COLUMN user_id INTEGER")
                self.conn.execute("ALTER TABLE repo_stars ADD COLUMN user_id INTEGER")
                
                # Update primary keys to include user_id (SQLite doesn't support modifying constraints)
                # The new CREATE TABLE statements handle this for new installs
                
                self.logger.info("Migrated database to multi-user schema")
            
        except sqlite3.Error as e:
            # Migration already completed or failed - continue with existing schema
            self.logger.debug(f"Migration note: {e}")
            pass

    def _execute_query(self, query: str, params: tuple = (), fetch_all: bool = True):
        """Execute a database query with consistent error handling."""
        try:
            with self.conn:
                cursor = self.conn.execute(query, params)
                if fetch_all:
                    return cursor.fetchall()
                return cursor.fetchone()
        except sqlite3.Error as e:
            self.logger.error(f"Database query failed: {e}")
            return [] if fetch_all else None

    def get_existing_timestamps(self, repo: str) -> List[str]:
        """Get all existing timestamps for a given repository."""
        rows = self._execute_query(
            "SELECT timestamp FROM clone_history WHERE repo = ?",
            (repo,)
        )
        return [row['timestamp'] for row in rows] if rows else []

    def insert_clone_records(self, repo: str, records: List['CloneRecord']):
        """Insert new clone records into the database."""
        if not records:
            return

        insert_data = [
            (repo, record.timestamp, record.count, record.uniques) for record in records
        ]

        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO clone_history (repo, timestamp, count, uniques) VALUES (?, ?, ?, ?)",
                    insert_data
                )
            self.logger.info(f"Inserted {len(records)} new records for {repo}.")
        except sqlite3.Error as e:
            self.logger.error(f"Failed to insert clone records for {repo}: {e}")
            raise

    def upsert_clone_records(self, repo: str, records: List['CloneRecord']):
        """Insert or update clone records in the database."""
        if not records:
            return

        upsert_data = [
            (repo, record.timestamp, record.count, record.uniques) for record in records
        ]

        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO clone_history (repo, timestamp, count, uniques) VALUES (?, ?, ?, ?)",
                    upsert_data
                )
            self.logger.info(f"Upserted {len(records)} records for {repo}.")
        except sqlite3.Error as e:
            self.logger.error(f"Failed to upsert clone records for {repo}: {e}")
            raise


    def upsert_view_records(self, repo: str, records: List['ViewRecord']):
        """Insert or update view records in the database."""
        if not records:
            return

        upsert_data = [
            (repo, record.timestamp, record.count, record.uniques) for record in records
        ]

        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO view_history (repo, timestamp, count, uniques) VALUES (?, ?, ?, ?)",
                    upsert_data
                )
            self.logger.info(f"Upserted {len(records)} view records for {repo}.")
        except sqlite3.Error as e:
            self.logger.error(f"Failed to upsert view records for {repo}: {e}")
            raise

    def get_tracked_repos(self) -> List[Dict[str, str]]:
        """Get all actively tracked repositories with their owner types."""
        rows = self._execute_query(
            "SELECT repo_name, owner_type FROM tracked_repos WHERE is_active = 1"
        )
        return [{"repo_name": row[0], "owner_type": row[1] if row[1] else "user"} for row in rows] if rows else []
        
    def get_tracked_repo_names(self) -> List[str]:
        """Get just the repository names (for backward compatibility)."""
        repos = self.get_tracked_repos()
        return [repo["repo_name"] for repo in repos]

    def add_tracked_repo(self, repo_name: str, owner_type: str = 'user') -> bool:
        """Add a repository to tracking with specified owner type."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO tracked_repos (repo_name, added_at, is_active, owner_type) VALUES (?, ?, 1, ?)",
                    (repo_name, datetime.now().isoformat(), owner_type)
                )
            self.logger.info(f"Added {repo_name} to tracked repos (owner_type: {owner_type}).")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to add tracked repo {repo_name}: {e}")
            return False

    def remove_tracked_repo(self, repo_name: str) -> bool:
        """Remove a repository from tracking."""
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE tracked_repos SET is_active = 0 WHERE repo_name = ?",
                    (repo_name,)
                )
            self.logger.info(f"Removed {repo_name} from tracked repos.")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to remove tracked repo {repo_name}: {e}")
            return False

    def update_tracked_repo(self, repo_name: str):
        """Update the last sync time for a tracked repo."""
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE tracked_repos SET last_sync = ? WHERE repo_name = ?",
                    (datetime.utcnow().isoformat() + 'Z', repo_name)
                )
            self.logger.debug(f"Updated last_sync for {repo_name}")
        except sqlite3.Error as e:
            self.logger.error(f"Failed to update last_sync for {repo_name}: {e}")

    def update_repo_stars(self, repo: str, star_count: int) -> bool:
        """Update star count for a repository."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO repo_stars (repo, star_count, last_updated) VALUES (?, ?, ?)",
                    (repo, star_count, datetime.now().isoformat())
                )
            self.logger.info(f"Updated star count for {repo}: {star_count}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to update star count for {repo}: {e}")
            return False

    def get_repo_stars(self, repo: str) -> Optional[int]:
        """Get star count for a repository."""
        row = self._execute_query(
            "SELECT star_count FROM repo_stars WHERE repo = ?",
            (repo,),
            fetch_all=False
        )
        return row[0] if row else None

    def export_database(self) -> Dict:
        """Export the complete database to a dictionary."""
        try:
            with self.conn:
                export_data = {
                    "export_timestamp": datetime.now().isoformat(),
                    "version": "1.0",
                    "clone_history": [],
                    "view_history": [],
                    "tracked_repos": [],
                    "repo_stars": []
                }

                # Export clone history
                cursor = self.conn.execute(
                    "SELECT repo, timestamp, count, uniques FROM clone_history ORDER BY repo, timestamp"
                )
                for row in cursor.fetchall():
                    export_data["clone_history"].append({
                        "repo": row["repo"],
                        "timestamp": row["timestamp"],
                        "count": row["count"],
                        "uniques": row["uniques"]
                    })

                # Export view history
                cursor = self.conn.execute(
                    "SELECT repo, timestamp, count, uniques FROM view_history ORDER BY repo, timestamp"
                )
                for row in cursor.fetchall():
                    export_data["view_history"].append({
                        "repo": row["repo"],
                        "timestamp": row["timestamp"],
                        "count": row["count"],
                        "uniques": row["uniques"]
                    })

                # Export tracked repos
                cursor = self.conn.execute("SELECT repo_name, added_at, is_active, owner_type FROM tracked_repos")
                for row in cursor.fetchall():
                    export_data["tracked_repos"].append({
                        "repo_name": row["repo_name"],
                        "added_at": row["added_at"],
                        "is_active": row["is_active"],
                        "owner_type": row["owner_type"] if row["owner_type"] else "user"
                    })

                # Export star counts
                cursor = self.conn.execute("SELECT repo, star_count, last_updated FROM repo_stars")
                for row in cursor.fetchall():
                    export_data["repo_stars"].append({
                        "repo": row["repo"],
                        "star_count": row["star_count"],
                        "last_updated": row["last_updated"]
                    })

                self.logger.info(
                    f"Database exported successfully with {len(export_data['clone_history'])} clone records, "
                    f"{len(export_data['view_history'])} view records, "
                    f"{len(export_data['tracked_repos'])} tracked repos, "
                    f"and {len(export_data['repo_stars'])} star records"
                )
                return export_data

        except sqlite3.Error as e:
            self.logger.error(f"Failed to export database: {e}")
            raise

    def import_database(self, import_data: Dict, replace_existing: bool = False) -> bool:
        """Import data from a dictionary into the database."""
        try:
            if replace_existing:
                self.logger.info("Clearing existing data before import...")
                with self.conn:
                    self.conn.execute("DELETE FROM clone_history")
                    self.conn.execute("DELETE FROM view_history")
                    self.conn.execute("DELETE FROM tracked_repos")
                    self.conn.execute("DELETE FROM repo_stars")

            # Import clone history
            clone_records = import_data.get("clone_history", [])
            if clone_records:
                with self.conn:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO clone_history (repo, timestamp, count, uniques) VALUES (?, ?, ?, ?)",
                        [(record["repo"], record["timestamp"], record["count"], record["uniques"])
                         for record in clone_records]
                    )
                self.logger.info(f"Imported {len(clone_records)} clone history records")

            # Import view history
            view_records = import_data.get("view_history", [])
            if view_records:
                with self.conn:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO view_history (repo, timestamp, count, uniques) VALUES (?, ?, ?, ?)",
                        [(record["repo"], record["timestamp"], record["count"], record["uniques"])
                         for record in view_records]
                    )
                self.logger.info(f"Imported {len(view_records)} view history records")

            # Import tracked repos
            tracked_repos = import_data.get("tracked_repos", [])
            if tracked_repos:
                with self.conn:
                    self.conn.executemany(
                        "INSERT OR REPLACE INTO tracked_repos (repo_name, added_at, is_active) VALUES (?, ?, ?)",
                        [(record["repo_name"], record["added_at"], record["is_active"])
                         for record in tracked_repos]
                    )
                self.logger.info(f"Imported {len(tracked_repos)} tracked repo records")

            # Import star counts
            star_records = import_data.get("repo_stars", [])
            if star_records:
                with self.conn:
                    self.conn.executemany(
                        "INSERT OR REPLACE INTO repo_stars (repo, star_count, last_updated) VALUES (?, ?, ?)",
                        [(record["repo"], record["star_count"], record["last_updated"])
                         for record in star_records]
                    )
                self.logger.info(f"Imported {len(star_records)} star count records")

            self.logger.info("Database import completed successfully")
            return True

        except (sqlite3.Error, KeyError, TypeError) as e:
            self.logger.error(f"Failed to import database: {e}")
            return False

    # User management methods for OAuth support
    
    def create_or_update_user(self, github_user_data: dict, github_token: str) -> int:
        """Create or update a user record from GitHub OAuth data."""
        try:
            with self.conn:
                # Check if user already exists
                existing_user = self._execute_query(
                    "SELECT id FROM users WHERE github_id = ?",
                    (github_user_data['id'],),
                    fetch_all=False
                )
                
                current_time = datetime.now().isoformat()
                
                if existing_user:
                    # Update existing user
                    user_id = existing_user[0]
                    self.conn.execute("""
                        UPDATE users SET 
                            github_username = ?, github_name = ?, github_email = ?,
                            github_avatar_url = ?, github_token = ?, last_login = ?
                        WHERE id = ?
                    """, (
                        github_user_data['login'],
                        github_user_data.get('name'),
                        github_user_data.get('email'),
                        github_user_data.get('avatar_url'),
                        github_token,
                        current_time,
                        user_id
                    ))
                else:
                    # Create new user
                    cursor = self.conn.execute("""
                        INSERT INTO users (
                            github_id, github_username, github_name, github_email,
                            github_avatar_url, github_token, created_at, last_login
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        github_user_data['id'],
                        github_user_data['login'],
                        github_user_data.get('name'),
                        github_user_data.get('email'),
                        github_user_data.get('avatar_url'),
                        github_token,
                        current_time,
                        current_time
                    ))
                    user_id = cursor.lastrowid
                
                self.logger.info(f"Created/updated user: {github_user_data['login']} (ID: {user_id})")
                return user_id
                
        except sqlite3.Error as e:
            self.logger.error(f"Failed to create/update user: {e}")
            raise
    
    def get_user_by_github_id(self, github_id: int) -> Optional[dict]:
        """Get user record by GitHub ID."""
        row = self._execute_query(
            "SELECT id, github_id, github_username, github_name, github_email, github_avatar_url, github_token FROM users WHERE github_id = ?",
            (github_id,),
            fetch_all=False
        )
        if row:
            return {
                'id': row[0],
                'github_id': row[1],
                'github_username': row[2],
                'github_name': row[3],
                'github_email': row[4],
                'github_avatar_url': row[5],
                'github_token': row[6]
            }
        return None
    
    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        """Get user record by internal user ID."""
        row = self._execute_query(
            "SELECT id, github_id, github_username, github_name, github_email, github_avatar_url, github_token FROM users WHERE id = ?",
            (user_id,),
            fetch_all=False
        )
        if row:
            return {
                'id': row[0],
                'github_id': row[1],
                'github_username': row[2],
                'github_name': row[3],
                'github_email': row[4],
                'github_avatar_url': row[5],
                'github_token': row[6]
            }
        return None

    # Updated methods to support user context
    
    def get_tracked_repos(self, user_id: Optional[int] = None) -> List[Dict[str, str]]:
        """Get tracked repositories for a specific user or legacy single-user."""
        if user_id is not None:
            # Multi-user mode: get repos for specific user
            query = "SELECT repo_name, owner_type FROM tracked_repos WHERE is_active = 1 AND user_id = ?"
            params = (user_id,)
        else:
            # Legacy mode: get repos with no user_id (backward compatibility)
            query = "SELECT repo_name, owner_type FROM tracked_repos WHERE is_active = 1 AND user_id IS NULL"
            params = ()
            
        rows = self._execute_query(query, params)
        return [{"repo_name": row[0], "owner_type": row[1] if row[1] else "user"} for row in rows] if rows else []
    
    def add_tracked_repo(self, repo_name: str, owner_type: str = 'user', user_id: Optional[int] = None) -> bool:
        """Add a repository to tracking with optional user context."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO tracked_repos (repo_name, added_at, is_active, owner_type, user_id) VALUES (?, ?, 1, ?, ?)",
                    (repo_name, datetime.now().isoformat(), owner_type, user_id)
                )
            user_context = f" for user {user_id}" if user_id else " (legacy)"
            self.logger.info(f"Added {repo_name} to tracked repos (owner_type: {owner_type}){user_context}.")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to add tracked repo {repo_name}: {e}")
            return False

    def remove_tracked_repo(self, repo_name: str, user_id: Optional[int] = None) -> bool:
        """Remove a repository from tracking with optional user context."""
        try:
            with self.conn:
                if user_id is not None:
                    self.conn.execute(
                        "UPDATE tracked_repos SET is_active = 0 WHERE repo_name = ? AND user_id = ?",
                        (repo_name, user_id)
                    )
                else:
                    self.conn.execute(
                        "UPDATE tracked_repos SET is_active = 0 WHERE repo_name = ? AND user_id IS NULL",
                        (repo_name,)
                    )
            user_context = f" for user {user_id}" if user_id else " (legacy)"
            self.logger.info(f"Removed {repo_name} from tracked repos{user_context}.")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to remove tracked repo {repo_name}: {e}")
            return False


class GitHubStatsTracker:
    """Main class for tracking GitHub repository clone statistics."""

    def __init__(self, github_token: str, github_username: str, repos: List[str], db_manager: 'DatabaseManager'):
        """
        Initialize the stats tracker.

        Args:
            github_token: GitHub Personal Access Token
            github_username: GitHub username
            repos: List of repository names to track
            db_manager: Instance of DatabaseManager
        """
        self.github_token = github_token
        self.github_username = github_username
        self.github_org = os.environ.get('GITHUB_ORG')
        self.repos = repos
        self.db_manager = db_manager
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        })

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def _get_repo_path(self, repo_name: str, owner_type: str = 'user') -> str:
        """
        Determine the full repository path based on owner type.
        
        Args:
            repo_name: Repository name, can be 'repo' or 'owner/repo'
            owner_type: Either 'user' or 'org'
            
        Returns:
            Full repository path as 'owner/repo'
        """
        # If repo already contains slash, use as-is
        if '/' in repo_name:
            return repo_name
        
        # Determine owner based on type
        if owner_type == 'org':
            if not self.github_org:
                self.logger.warning(f"Repository {repo_name} marked as org but GITHUB_ORG not set, falling back to username")
                owner = self.github_username
            else:
                owner = self.github_org
        else:  # owner_type == 'user' or default
            owner = self.github_username
            
        return f"{owner}/{repo_name}"

    def _fetch_clone_data(self, repo: str, owner_type: str = 'user') -> Dict[str, any]:
        """Fetch clone data from GitHub API."""
        repo_path = self._get_repo_path(repo, owner_type)
        url = f"https://api.github.com/repos/{repo_path}/traffic/clones"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            self.logger.error(f"Error fetching clone data for {repo}: {e}")
            raise

    def _fetch_view_data(self, repo: str, owner_type: str = 'user') -> Dict[str, any]:
        """Fetch view data from GitHub API."""
        repo_path = self._get_repo_path(repo, owner_type)
        url = f"https://api.github.com/repos/{repo_path}/traffic/views"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            self.logger.error(f"Error fetching view data for {repo}: {e}")
            raise

    def _fetch_repo_metadata(self, repo: str, owner_type: str = 'user') -> Dict[str, any]:
        """Fetch repository metadata from GitHub API."""
        repo_path = self._get_repo_path(repo, owner_type)
        url = f"https://api.github.com/repos/{repo_path}"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            self.logger.error(f"Error fetching metadata for {repo}: {e}")
            raise

    def _update_repository(self, repo: str, owner_type: str = 'user') -> None:
        """Update clone and view data for a single repository."""
        self.logger.info(f"Updating data for {repo}")

        # Fetch new data from GitHub
        self.logger.info("Fetching data from GitHub...")
        try:
            # Fetch clone data
            clone_data = self._fetch_clone_data(repo, owner_type)
            clone_entries = clone_data.get("clones", [])

            # Process all clone entries (GitHub may retroactively update statistics)
            clone_records_to_upsert = []
            for entry in clone_entries:
                new_record = CloneRecord.from_github_entry(entry)
                clone_records_to_upsert.append(new_record)
                self.logger.debug(f"Processing clone record for {repo}: {new_record}")

            # Upsert all clone records into the database
            if clone_records_to_upsert:
                self.db_manager.upsert_clone_records(repo, clone_records_to_upsert)
                self.logger.info(f"Updated {len(clone_records_to_upsert)} clone records for {repo}")
            else:
                self.logger.info(f"No clone records to update for {repo}")

            # Fetch view data
            view_data = self._fetch_view_data(repo, owner_type)
            view_entries = view_data.get("views", [])

            # Process all view entries
            view_records_to_upsert = []
            for entry in view_entries:
                new_record = ViewRecord.from_github_entry(entry)
                view_records_to_upsert.append(new_record)
                self.logger.debug(f"Processing view record for {repo}: {new_record}")

            # Upsert all view records into the database
            if view_records_to_upsert:
                self.db_manager.upsert_view_records(repo, view_records_to_upsert)
                self.logger.info(f"Updated {len(view_records_to_upsert)} view records for {repo}")
            else:
                self.logger.info(f"No view records to update for {repo}")

            # Fetch and update star count
            self.logger.info("Fetching repository metadata...")
            metadata = self._fetch_repo_metadata(repo, owner_type)
            star_count = metadata.get("stargazers_count", 0)
            self.db_manager.update_repo_stars(repo, star_count)

            # Update the last_sync timestamp for this repo
            self.db_manager.update_tracked_repo(repo)

            self.logger.info(f"Update for {repo} completed successfully")

        except Exception as e:
            self.logger.error(f"Failed to update {repo}: {e}")
            raise

    def update_all_repositories(self) -> None:
        """Update clone data for all configured repositories."""
        self.logger.info("Starting update for all repositories")

        # Get tracked repos from database if available, otherwise use configured repos
        tracked_repos = self.db_manager.get_tracked_repos()
        
        if tracked_repos:
            # Use tracked repos with owner type info
            repos_to_update = tracked_repos
        else:
            # Convert legacy repo list to new format
            repos_to_update = [{"repo_name": repo, "owner_type": "user"} for repo in self.repos]

        for repo_info in repos_to_update:
            try:
                repo_name = repo_info["repo_name"]
                owner_type = repo_info.get("owner_type", "user")
                
                print("=" * 60)
                print(f"   Updating data for {repo_name}")
                print("=" * 60)

                self._update_repository(repo_name, owner_type)
                print()

            except Exception as e:
                self.logger.error(f"Failed to update {repo}: {e}")
                continue

        self.logger.info("Finished updating all repositories")


def load_configuration() -> Tuple[str, str, List[str], str]:
    """Load configuration from environment variables."""
    github_token = os.environ.get('GITHUB_TOKEN')
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable not set.")

    github_username = os.environ.get('GITHUB_USERNAME')
    if not github_username:
        raise ValueError("GITHUB_USERNAME environment variable not set.")

    # Default repository list (only used for initial setup)
    repos = []

    db_path = os.environ.get('DATABASE_PATH', 'github_stats.db')

    return github_token, github_username, repos, db_path


def run_sync():
    """Runs the GitHub stats synchronization."""
    logger = logging.getLogger(__name__)
    try:
        # Load configuration
        github_token, github_username, repos, db_path = load_configuration()

        # Get appropriate database manager
        from .db_factory import get_database_manager
        db_manager = get_database_manager()
        
        # Setup and run tracker
        with db_manager:
            db_manager.setup_database()
            # No default repos to migrate since repos list is empty
            tracker = GitHubStatsTracker(github_token, github_username, repos, db_manager)
            tracker.update_all_repositories()
        logger.info("Sync successful")
        return True, "Sync successful"
    except Exception as e:
        logger.error(f"Application error: {e}")
        return False, str(e)


def main() -> None:
    """Main entry point of the application."""
    try:
        run_sync()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")


if __name__ == "__main__":
    main()
