# -*- coding: utf-8 -*-
"""Git service for repository management within ebprocess-development."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from ebdev.config import config
from ebdev.core.exceptions import GitServiceError
from ebdev.core.logger import get_logger

logger = get_logger(__name__)


class GitConflictError(Exception):
    """Raised when a Git merge or operation results in unresolved conflicts."""


class GitService:
    """Service for handling Git operations like cloning, fetching, and pushing."""

    def __init__(self, repo_path: str | Path):
        """
        Initialize the Git service.

        Args:
            repo_path: Local path to the Git repository.
        """
        self.repo_path = Path(repo_path)
        self.env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": config.GIT_USER or "EBProcess Bot",
            "GIT_AUTHOR_EMAIL": config.GIT_USER_EMAIL,
            "GIT_COMMITTER_NAME": config.GIT_USER or "EBProcess Bot",
            "GIT_COMMITTER_EMAIL": config.GIT_USER_EMAIL,
        }

    def _run(
        self, args: list[str], check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """Helper to run git commands with standard environment and working directory."""
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo_path),
            check=check,
            env=self.env,
            capture_output=capture,
            text=True,
        )

    def inject_token(self, repo_url: str) -> str:
        """Inject git credentials into the clone URL with proper encoding."""
        if not repo_url or "https://" not in repo_url:
            return repo_url

        try:
            parsed = urlparse(repo_url)
            host = parsed.hostname or ""

            # Host-specific credential selection
            if "bitbucket.org" in host:
                token = config.BITBUCKET_APP_PASSWORD
                user = config.BITBUCKET_USERNAME
            elif "github.com" in host:
                token = config.GITHUB_TOKEN or config.GIT_TOKEN
                user = config.GITHUB_USER or config.GIT_USER
            else:
                # Generic fallback
                token = config.GIT_TOKEN
                user = config.GIT_USER

            if not token or not user:
                logger.debug(f"Missing credentials for host {host}, skipping injection")
                return repo_url

            safe_user = quote(user, safe="")
            safe_token = quote(token, safe="")

            new_netloc = f"{safe_user}:{safe_token}@{host}"
            if parsed.port:
                new_netloc += f":{parsed.port}"

            return urlunparse(parsed._replace(netloc=new_netloc))
        except Exception as e:
            logger.warning(f"Failed to inject token into URL: {e}")
            return repo_url

    def is_git_repo(self) -> bool:
        """Check if the configured path is a valid Git repository."""
        return (self.repo_path / ".git").exists()

    def clone_or_fetch(
        self, target_repo: str, starter_kit_url: str | None = None
    ) -> list[str]:
        """Clones a starter kit, initializes a new repo, or fetches an existing one."""
        log = []
        target_clone_url = self.inject_token(target_repo) if target_repo else ""

        if not self.is_git_repo():
            if starter_kit_url and target_repo:
                starter_clone_url = self.inject_token(starter_kit_url)

                if self.repo_path.exists() and any(self.repo_path.iterdir()):
                    shutil.rmtree(self.repo_path)
                self.repo_path.mkdir(parents=True, exist_ok=True)

                try:
                    logger.info(f"Cloning starter kit from {starter_kit_url}...")
                    subprocess.run(["git", "clone", starter_clone_url, str(self.repo_path)], check=True, env=self.env)
                except subprocess.CalledProcessError as e:
                    raise GitServiceError(f"Failed to clone starter kit: {e.stderr}")

                self._run(["remote", "remove", "origin"])
                self._run(["remote", "add", "origin", target_clone_url])

                max_retries = 5
                for i in range(max_retries):
                    try:
                        self._run(["push", "-u", "origin", "--all"])
                        break
                    except subprocess.CalledProcessError as e:
                        if i == max_retries - 1:
                            jls_extract_var = GitServiceError
                            raise jls_extract_var(
                                f"Failed to push to remote after {max_retries} attempts: {e.stderr}"
                            )
                        logger.warning(f"Push failed, retrying in 5s... ({i+1}/{max_retries})")
                        time.sleep(5)
                log.append(f"Seeded repository with {starter_kit_url}")
            else:
                self.repo_path.mkdir(parents=True, exist_ok=True)
                self._run(["init"])
                if target_clone_url:
                    self._run(["remote", "add", "origin", target_clone_url])
                log.append("Initialized new empty Git repository locally.")
        else:
            if target_clone_url:
                self._run(["remote", "set-url", "origin", target_clone_url])
            self._run(["fetch", "origin"])
            log.append("Fetched updates from repository.")

        return log

    def checkout_branch(self, branch_name: str) -> str:
        """Checks out an existing branch or creates a new one."""
        res = self._run(["branch", "--list", branch_name], check=False)
        if branch_name in res.stdout:
            self._run(["checkout", branch_name])
            return f"Checked out existing branch: {branch_name}"

        checkout_res = self._run(["checkout", branch_name], check=False)
        if checkout_res.returncode != 0:
            res = self._run(["rev-parse", "HEAD"], check=False)
            if res.returncode == 0:
                self._run(["checkout", "-b", branch_name])
                return f"Created and checked out feature branch: {branch_name}"
            return "Empty repository detected. Branch will be created on first commit."

        return f"Checked out branch from remote: {branch_name}"

    def sync_with_main(self, default_branch: str = "main") -> list[str]:
        """Fetches the latest default branch and merges it into the current branch."""
        log = []

        if self._run(["rev-parse", "HEAD"], check=False).returncode != 0:
            return log

        fetch_res = self._run(
            ["fetch", "origin", f"{default_branch}:{default_branch}"], check=False
        )
        if fetch_res.returncode != 0:
            log.append(
                f"Could not fetch origin/{default_branch} (it may not exist yet). Skipping sync."
            )
            return log

        merge_res = self._run(["merge", f"origin/{default_branch}"], check=False)
        if merge_res.returncode != 0:
            self._run(["merge", "--abort"], check=False)
            error_msg = (
                f"Merge conflict detected when syncing with origin/{default_branch}."
            )
            logger.error(error_msg)
            raise GitConflictError(error_msg)

        if "Already up to date" not in merge_res.stdout:
            log.append(f"Synced branch with latest origin/{default_branch}.")

        return log

    def has_changes(self) -> bool:
        """Checks if there are uncommitted changes in the repository."""
        status_res = self._run(["status", "--porcelain"])
        return bool(status_res.stdout.strip())

    def commit_all(self, message: str) -> None:
        """Stages and commits all changes."""
        if not self.has_changes():
            return
        self._run(["add", "."])
        self._run(["commit", "-m", message])

    def push(self, branch_name: str, repo_url: str) -> None:
        """Pushes the current branch to the remote."""
        remote_target = self.inject_token(repo_url) if repo_url else "origin"
        logger.info(f"Pushing branch {branch_name} to remote...")
        self._run(["push", remote_target, branch_name])


class RemoteRepoService:
    """Service to check, create, and verify remote repositories in Bitbucket or GitHub."""

    @staticmethod
    def parse_repo_url(repo_url: str) -> tuple[str, str, str] | None:
        """
        Parse repo URL into (provider, owner/workspace, slug).
        Returns None if not parseable.
        """
        if not repo_url:
            return None
        
        # Remove git ssh prefix or protocol prefix
        clean = repo_url.strip().replace(".git", "")
        
        provider = "bitbucket" if "bitbucket" in clean.lower() else "github"
        
        # If SSH: git@bitbucket.org:workspace/slug
        if "git@" in clean:
            # Split after :
            parts = clean.split(":")[-1].split("/")
            if len(parts) >= 2:
                return provider, parts[-2], parts[-1]
        else:
            # If HTTPS: https://bitbucket.org/workspace/slug
            # parse path
            parsed = urlparse(repo_url)
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 2:
                return provider, path_parts[-2], path_parts[-1]
                
        return None

    @classmethod
    async def ensure_repo_exists(cls, repo_url: str, jira_project_key: str = "PROJ") -> bool:
        """
        Check if remote repository exists. If not, attempt to create it.
        Returns True if repo exists or was successfully created, False otherwise.
        """
        parsed = cls.parse_repo_url(repo_url)
        if not parsed:
            logger.warning(f"Could not parse repository URL: {repo_url}. Skipping auto-creation checks.")
            return True
            
        provider, workspace, slug = parsed
        logger.info(f"Checking remote repository: provider={provider}, workspace/owner={workspace}, slug={slug}")
        
        import httpx
        
        if provider == "bitbucket":
            # Auth
            user = config.BITBUCKET_USERNAME or config.GIT_USER
            token = config.BITBUCKET_APP_PASSWORD or config.GIT_TOKEN
            auth = (user, token) if user and token else None
            
            get_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}"
            
            async with httpx.AsyncClient() as client:
                try:
                    res = await client.get(get_url, auth=auth)
                    if res.status_code == 200:
                        logger.info(f"Bitbucket repository '{workspace}/{slug}' exists.")
                        return True
                    elif res.status_code == 404:
                        logger.info(f"Bitbucket repository '{workspace}/{slug}' not found. Creating...")
                        create_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}"
                        payload = {
                            "scm": "git",
                            "is_private": True,
                            "project": {"key": jira_project_key}
                        }
                        create_res = await client.post(create_url, json=payload, auth=auth)
                        if create_res.status_code in (200, 201):
                            logger.info(f"Bitbucket repository '{workspace}/{slug}' successfully created.")
                            return True
                        else:
                            logger.error(f"Failed to create Bitbucket repository: {create_res.status_code} - {create_res.text}")
                            return False
                    else:
                        logger.warning(f"Unexpected Bitbucket status check response code: {res.status_code}")
                        return True # fallback to let git clone handle it
                except Exception as e:
                    logger.error(f"Error during Bitbucket remote repository check/create: {e}")
                    return True
                    
        elif provider == "github":
            token = config.GITHUB_TOKEN or config.GIT_TOKEN
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            if token:
                headers["Authorization"] = f"token {token}"
                
            get_url = f"https://api.github.com/repos/{workspace}/{slug}"
            
            async with httpx.AsyncClient() as client:
                try:
                    res = await client.get(get_url, headers=headers)
                    if res.status_code == 200:
                        logger.info(f"GitHub repository '{workspace}/{slug}' exists.")
                        return True
                    elif res.status_code == 404:
                        logger.info(f"GitHub repository '{workspace}/{slug}' not found. Creating...")
                        # If workspace matches config username, it's a personal repo. Otherwise it's an org.
                        user = config.GITHUB_USER or config.GIT_USER
                        
                        if workspace.lower() == user.lower():
                            create_url = "https://api.github.com/user/repos"
                        else:
                            create_url = f"https://api.github.com/orgs/{workspace}/repos"
                            
                        payload = {
                            "name": slug,
                            "private": True
                        }
                        create_res = await client.post(create_url, json=payload, headers=headers)
                        if create_res.status_code in (200, 201):
                            logger.info(f"GitHub repository '{workspace}/{slug}' successfully created.")
                            return True
                        else:
                            logger.error(f"Failed to create GitHub repository: {create_res.status_code} - {create_res.text}")
                            return False
                    else:
                        logger.warning(f"Unexpected GitHub status check response code: {res.status_code}")
                        return True
                except Exception as e:
                    logger.error(f"Error during GitHub remote repository check/create: {e}")
                    return True
                    
        return True
