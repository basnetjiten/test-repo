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
                    raise RuntimeError(f"Failed to clone starter kit: {e.stderr}")

                self._run(["remote", "remove", "origin"])
                self._run(["remote", "add", "origin", target_clone_url])

                max_retries = 5
                for i in range(max_retries):
                    try:
                        self._run(["push", "-u", "origin", "--all"])
                        break
                    except subprocess.CalledProcessError as e:
                        if i == max_retries - 1:
                            raise RuntimeError(
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
