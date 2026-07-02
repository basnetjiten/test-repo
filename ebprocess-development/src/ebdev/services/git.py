# -*- coding: utf-8 -*-
"""
git.py
======
Git service for repository management within ebprocess-development.

Responsibilities
----------------
* Handle local Git actions (init, clone, fetch, checkout, branch merge, status, commit, push).
* Inject authorization tokens into remote repository URLs.
* Remotely verify and create repositories in GitHub or Bitbucket via HTTP API clients.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from ebdev.config import config
from ebdev.core.exceptions import GitServiceError

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Bitbucket REST API v2 base path.
BITBUCKET_API_BASE: str = "https://api.bitbucket.org/2.0"

#: GitHub REST API v3 base path.
GITHUB_API_BASE: str = "https://api.github.com"


# ---------------------------------------------------------------------------
# Domain Exceptions
# ---------------------------------------------------------------------------
class GitConflictError(Exception):
    """Raised when a Git merge or operation results in unresolved conflicts."""


# ---------------------------------------------------------------------------
# Git Operations Service
# ---------------------------------------------------------------------------
class GitService:
    """Service for handling Git operations like cloning, fetching, and pushing."""

    def __init__(self, repo_path: str | Path):
        """
        Initialize the Git service.

        Parameters
        ----------
        repo_path : str | Path
            Local path to the Git repository.
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
        """
        Inject git credentials into the clone URL with proper encoding.

        Parameters
        ----------
        repo_url : str
            The raw remote repository URL.

        Returns
        -------
        str
            The repository URL containing injected authorization credentials.
        """
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
                logger.debug("Missing credentials for host %s, skipping injection", host)
                return repo_url

            safe_user = quote(user, safe="")
            safe_token = quote(token, safe="")

            new_netloc = f"{safe_user}:{safe_token}@{host}"
            if parsed.port:
                new_netloc += f":{parsed.port}"

            return urlunparse(parsed._replace(netloc=new_netloc))
        except (ValueError, TypeError) as e:
            logger.warning("Failed to inject token into URL: %s", e)
            return repo_url

    def is_git_repo(self) -> bool:
        """
        Check if the configured path is a valid Git repository.

        Returns
        -------
        bool
            True if .git directory exists.
        """
        return (self.repo_path / ".git").exists()

    def has_commits(self) -> bool:
        """
        Check if the repository has any commits.

        Returns
        -------
        bool
            True if HEAD points to a valid commit.
        """
        if not self.is_git_repo():
            return False
        return self._run(["rev-parse", "HEAD"], check=False).returncode == 0

    def clone_or_fetch(
        self, target_repo: str, starter_kit_url: str | None = None
    ) -> list[str]:
        """
        Clone a starter kit, initialize a new repo, or fetch an existing one.

        Parameters
        ----------
        target_repo : str
            The remote URL of the target repository.
        starter_kit_url : str | None
            Optional remote URL of the starter kit to clone.

        Returns
        -------
        list[str]
            A list of diagnostic log statements.

        Raises
        ------
        GitServiceError
            If cloning or initial push fails.
        """
        log = []
        target_clone_url = self.inject_token(target_repo) if target_repo else ""

        if not self.is_git_repo():
            if starter_kit_url and target_repo:
                starter_clone_url = self.inject_token(starter_kit_url)

                if self.repo_path.exists() and any(self.repo_path.iterdir()):
                    shutil.rmtree(self.repo_path)
                self.repo_path.mkdir(parents=True, exist_ok=True)

                try:
                    logger.info("Cloning starter kit from %s...", starter_kit_url)
                    subprocess.run(["git", "clone", starter_clone_url, str(self.repo_path)], check=True, env=self.env)
                except subprocess.CalledProcessError as e:
                    raise GitServiceError(f"Failed to clone starter kit: {e.stderr}") from e

                self._run(["remote", "remove", "origin"])
                self._run(["remote", "add", "origin", target_clone_url])

                max_retries = 2
                for i in range(max_retries):
                    try:
                        self._run(["push", "-u", "origin", "--all"])
                        break
                    except subprocess.CalledProcessError as e:
                        if i == max_retries - 1:
                            logger.warning(
                                "Pushing to remote failed, continuing locally since remote operations are secondary: %s",
                                e.stderr or e.stdout
                            )
                        else:
                            logger.warning("Push failed, retrying in 5s... (%s/%s)", i+1, max_retries)
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
        """
        Check out an existing branch or create a new one.

        Parameters
        ----------
        branch_name : str
            The name of the branch to check out.

        Returns
        -------
        str
            A descriptive log message.
        """
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
        """
        Fetch the latest default branch and merge it into the current branch.

        Parameters
        ----------
        default_branch : str
            The default upstream branch name.

        Returns
        -------
        list[str]
            A list of diagnostic logs.

        Raises
        ------
        GitConflictError
            If merge conflicts occur during the synchronization.
        """
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
        """
        Check if there are uncommitted changes in the repository.

        Returns
        -------
        bool
            True if changes exist.
        """
        status_res = self._run(["status", "--porcelain"])
        return bool(status_res.stdout.strip())

    def commit_all(self, message: str) -> None:
        """
        Stage and commit all changes.

        Parameters
        ----------
        message : str
            The commit message.
        """
        if not self.has_changes():
            return
        self._run(["add", "."])
        self._run(["commit", "-m", message])

    def push(self, branch_name: str, repo_url: str) -> None:
        """
        Push the current branch to the remote.

        Parameters
        ----------
        branch_name : str
            The local branch name.
        repo_url : str
            The remote repository clone URL.
        """
        remote_target = self.inject_token(repo_url) if repo_url else "origin"
        logger.info("Pushing branch %s to remote...", branch_name)
        self._run(["push", remote_target, branch_name])


# ---------------------------------------------------------------------------
# Remote Repository API Service
# ---------------------------------------------------------------------------
class RemoteRepoService:
    """Service to check, create, and verify remote repositories in Bitbucket or GitHub."""

    @staticmethod
    def parse_repo_url(repo_url: str) -> tuple[str, str, str] | None:
        """
        Parse repo URL into (provider, owner/workspace, slug).

        Parameters
        ----------
        repo_url : str
            The URL string to parse.

        Returns
        -------
        tuple[str, str, str] | None
            A tuple of (provider, owner/workspace, slug) or None if parsing fails.
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
            parsed = urlparse(repo_url)
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 2:
                return provider, path_parts[-2], path_parts[-1]
                
        return None

    @classmethod
    async def ensure_repo_exists(cls, repo_url: str, project_key: str = "PROJ") -> bool:
        """
        Check if remote repository exists. If not, attempt to create it.

        Parameters
        ----------
        repo_url : str
            The target repository URL.
        project_key : str
            The associated Bitbucket project key.

        Returns
        -------
        bool
            True if repository exists or was successfully created.
        """
        parsed = cls.parse_repo_url(repo_url)
        if not parsed:
            logger.warning("Could not parse repository URL: %s. Skipping auto-creation checks.", repo_url)
            return True
            
        provider, workspace, slug = parsed
        logger.info("Checking remote repository: provider=%s, workspace/owner=%s, slug=%s", provider, workspace, slug)
        
        import httpx
        
        if provider == "bitbucket":
            # Auth
            user = config.BITBUCKET_USERNAME or config.GIT_USER
            token = config.BITBUCKET_APP_PASSWORD or config.GIT_TOKEN
            auth = (user, token) if user and token else None
            
            get_url = f"{BITBUCKET_API_BASE}/repositories/{workspace}/{slug}"
            
            req_kwargs = {}
            if auth is not None:
                req_kwargs["auth"] = auth
                
            async with httpx.AsyncClient() as client:
                try:
                    res = await client.get(get_url, **req_kwargs)
                    if res.status_code == 200:
                        logger.info("Bitbucket repository '%s/%s' exists.", workspace, slug)
                        return True
                    elif res.status_code == 404:
                        logger.info("Bitbucket repository '%s/%s' not found. Creating...", workspace, slug)
                        create_url = f"{BITBUCKET_API_BASE}/repositories/{workspace}/{slug}"
                        payload = {
                            "scm": "git",
                            "is_private": True,
                            "project": {"key": project_key}
                        }
                        create_res = await client.post(create_url, json=payload, **req_kwargs)
                        if create_res.status_code in (200, 201):
                            logger.info("Bitbucket repository '%s/%s' successfully created.", workspace, slug)
                            return True
                        else:
                            logger.error("Failed to create Bitbucket repository: %s - %s", create_res.status_code, create_res.text)
                            return False
                    else:
                        logger.warning("Unexpected Bitbucket status check response code: %s", res.status_code)
                        return True # fallback to let git clone handle it
                except Exception as e:
                    logger.error("Error during Bitbucket remote repository check/create: %s", e)
                    return True
                    
        elif provider == "github":
            token = config.GITHUB_TOKEN or config.GIT_TOKEN
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            if token:
                headers["Authorization"] = f"token {token}"
                
            get_url = f"{GITHUB_API_BASE}/repos/{workspace}/{slug}"
            
            async with httpx.AsyncClient() as client:
                try:
                    res = await client.get(get_url, headers=headers)
                    if res.status_code == 200:
                        logger.info("GitHub repository '%s/%s' exists.", workspace, slug)
                        return True
                    elif res.status_code == 404:
                        logger.info("GitHub repository '%s/%s' not found. Creating...", workspace, slug)
                        user = config.GITHUB_USER or config.GIT_USER
                        
                        if workspace.lower() == user.lower():
                            create_url = f"{GITHUB_API_BASE}/user/repos"
                        else:
                            create_url = f"{GITHUB_API_BASE}/orgs/{workspace}/repos"
                            
                        payload = {
                            "name": slug,
                            "private": True
                        }
                        create_res = await client.post(create_url, json=payload, headers=headers)
                        if create_res.status_code in (200, 201):
                            logger.info("GitHub repository '%s/%s' successfully created.", workspace, slug)
                            return True
                        else:
                            logger.error("Failed to create GitHub repository: %s - %s", create_res.status_code, create_res.text)
                            return False
                    else:
                        logger.warning("Unexpected GitHub status check response code: %s", res.status_code)
                        return True
                except Exception as e:
                    logger.error("Error during GitHub remote repository check/create: %s", e)
                    return True
                    
        return True
