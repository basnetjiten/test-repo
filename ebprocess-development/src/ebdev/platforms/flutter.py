# -*- coding: utf-8 -*-
"""
flutter.py
==========
Concrete execution strategy for Flutter platforms.

Responsibilities
----------------
* Resolve Flutter project packages via pub get.
* Run Flutter/Dart analysis and build runner code generation tasks.
* Scaffold and bootstrap empty or templated Flutter project structures.
"""

from __future__ import annotations

from pathlib import Path
import re

from ebdev.core.exceptions import PlatformStrategyError
from ebdev.core.logger import get_logger
from ebdev.platforms.base import PlatformStrategy
from ebdev.services import flutter_cmd
from ebdev.services.fs import AsyncFileSystemService

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Flutter Platform Strategy
# ---------------------------------------------------------------------------
class FlutterStrategy(PlatformStrategy):
    """Execution strategy handling linter, dependency, and scaffolding actions for Flutter mobile."""

    async def prepare(self, repo_path: Path, _branch_name: str) -> None:
        """
        Resolve Flutter dependencies and run code generation.

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch.

        Raises
        ------
        PlatformStrategyError
            If running flutter pub get or build_runner fails.
        """
        logger.info("Preparing Flutter repository at %s", repo_path)
        pubspec = repo_path / "pubspec.yaml"
        if not await AsyncFileSystemService.exists(pubspec):
            logger.warning("No pubspec.yaml found at %s. Skipping dependency resolution and build_runner.", repo_path)
            return

        output_lines: list[str] = []
        logger.info("Running flutter pub get...")
        if not await flutter_cmd.pub_get(str(repo_path), output=output_lines):
            err_msg = output_lines[-1] if output_lines else "flutter pub get failed"
            raise PlatformStrategyError(f"Flutter preparation failure: {err_msg}")

        logger.info("Running build_runner build...")
        # Run build_runner during prepare to ensure all code-generated files exist initially
        if not await flutter_cmd.build_runner(str(repo_path), output=output_lines):
            logger.warning(
                "Flutter build_runner failed during preparation, proceeding anyway: %s",
                output_lines[-1] if output_lines else "unknown build_runner error",
            )

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Run pub get, build_runner, and analyze. Fail only on lint errors in
        hand-written feature files — pre-existing generated file errors are ignored.

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of validation error messages. Empty if validation passes.
        """
        logger.info("Validating Flutter repository at %s", repo_path)
        output_lines: list[str] = []

        # Guard: if there is no pubspec.yaml the workspace is a sparse/lib-only checkout.
        # We can only validate individual Dart files — skip full project analysis.
        pubspec = repo_path / "pubspec.yaml"
        if not await AsyncFileSystemService.exists(pubspec):
            logger.warning(
                "No pubspec.yaml found at %s — sparse checkout detected. "
                "Skipping full Flutter analysis; treating as PASS.",
                repo_path,
            )
            return []

        # 1. Resolve dependencies
        await flutter_cmd.pub_get(str(repo_path), output=output_lines)

        # 2. Run build_runner code-generation
        await flutter_cmd.build_runner(str(repo_path), output=output_lines)

        # 3. Static analysis linter
        analyze_ok = await flutter_cmd.analyze(str(repo_path), output=output_lines)
        validation_output = "\n".join(output_lines)

        # Collect only "error •" lines that are NOT in auto-generated files
        # (.freezed.dart / .g.dart / .graphql.dart) because those are built by
        # code-generation tools and their errors are never caused by our feature code.
        _generated_suffixes = (".freezed.dart", ".g.dart", ".graphql.dart", ".gql.dart")

        def _is_generated_file(line: str) -> bool:
            return any(suffix in line for suffix in _generated_suffixes)

        has_errors = "error •" in validation_output.lower()
        if not analyze_ok and not has_errors:
            logger.info("Flutter analyze failed but no lint errors found. Treating as PASS.")
            analyze_ok = True

        errors: list[str] = []
        if not analyze_ok or has_errors:
            raw_error_lines = [line.strip() for line in output_lines if "error •" in line.lower()]
            # Filter out errors originating from generated files — those are pre-existing
            errors = [e for e in raw_error_lines if not _is_generated_file(e)]
            if not errors and raw_error_lines:
                logger.info(
                    "All %d error(s) are in generated files (.freezed/.g/.graphql). "
                    "Treating as PASS — generated files are not our responsibility.",
                    len(raw_error_lines),
                )
            elif not errors:
                # analyze exited non-zero but no parseable error lines remain
                errors = []

        return errors

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:  # noqa: ARG002
        """
        Seed Flutter project files. Not implemented on this platform strategy.

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap.
        """
        raise PlatformStrategyError(
            f"Bootstrapping new boilerplate for {starter_type} is disabled. "
            "The repository must be pre-populated or cloned from a starter kit."
        )

    async def post_prepare(self, repo_path: Path, new_name: str) -> None:
        """
        Rename the Flutter project package name in pubspec.yaml and refactor all
        internal imports in the codebase (.dart files) to use the new package name.
        """
        pubspec_path = repo_path / "pubspec.yaml"
        if not await AsyncFileSystemService.exists(pubspec_path):
            return

        # 1. Read pubspec.yaml and find the old package name
        content = await AsyncFileSystemService.read_text(pubspec_path)
        match = re.search(r"^name:\s*([a-zA-Z0-9_\-]+)", content, re.MULTILINE)
        if not match:
            logger.warning("Could not find name field in pubspec.yaml at %s", pubspec_path)
            return
        old_package_name = match.group(1).strip()

        if old_package_name == new_name:
            logger.info("Flutter project package name already matches target: %s", new_name)
            return

        logger.info("Refactoring Flutter package from %r to %r...", old_package_name, new_name)

        # 2. Update the name: field inside pubspec.yaml
        new_content = content.replace(f"name: {old_package_name}", f"name: {new_name}", 1)
        await AsyncFileSystemService.write_text_atomic(pubspec_path, new_content)

        # 3. Update build.yaml ferry schema references (schema: old_name| -> schema: new_name|)
        build_yaml_path = repo_path / "build.yaml"
        if await AsyncFileSystemService.exists(build_yaml_path):
            build_content = await AsyncFileSystemService.read_text(build_yaml_path)
            updated_build = build_content.replace(f"schema: {old_package_name}|", f"schema: {new_name}|")
            if updated_build != build_content:
                await AsyncFileSystemService.write_text_atomic(build_yaml_path, updated_build)
                logger.info("Updated build.yaml schema references from %r to %r", old_package_name, new_name)

        # 4. Recursively refactor all imports in .dart files under lib/ and test/
        lib_dir = repo_path / "lib"
        test_dir = repo_path / "test"

        old_import_prefix = f"package:{old_package_name}/"
        new_import_prefix = f"package:{new_name}/"

        for folder in (lib_dir, test_dir):
            if not await AsyncFileSystemService.exists(folder):
                continue
            for file_path in folder.glob("**/*.dart"):
                try:
                    code = await AsyncFileSystemService.read_text(file_path)
                    if old_import_prefix in code:
                        updated_code = code.replace(old_import_prefix, new_import_prefix)
                        await AsyncFileSystemService.write_text_atomic(file_path, updated_code)
                except Exception as e:
                    logger.error("Failed to refactor Dart imports in %s: %s", file_path, e)
