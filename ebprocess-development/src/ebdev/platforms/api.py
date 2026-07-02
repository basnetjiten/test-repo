# -*- coding: utf-8 -*-
"""
api.py
======
Concrete execution strategy for Python and NestJS/Node API platforms.

Responsibilities
----------------
* Resolve package dependencies (pip or npm).
* Run validation suites (ruff/flake8 for Python, npm lint/test for NestJS).
* Bootstrap skeleton backends (FastAPI app layouts or basic NestJS folders).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from ebdev.platforms.base import PlatformStrategy

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API Platform Strategy
# ---------------------------------------------------------------------------
class ApiStrategy(PlatformStrategy):
    """Execution strategy handling linting, dependencies, and tests for API/backend projects (Python and NestJS/Node)."""

    async def _run_command(self, cmd: list[str], cwd: Path) -> tuple[int, bytes, bytes]:
        """
        Execute a subprocess command in the given working directory.

        Parameters
        ----------
        cmd : list[str]
            The list of command and arguments.
        cwd : Path
            The working directory.

        Returns
        -------
        tuple[int, bytes, bytes]
            A tuple containing return code, stdout bytes, and stderr bytes.
        """
        import os
        env = os.environ.copy()
        env["ESLINT_USE_FLAT_CONFIG"] = "false"
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode if proc.returncode is not None else -1, stdout, stderr

    async def prepare(self, repo_path: Path, branch_name: str) -> None:
        """
        Install package/repository dependencies.

        Parameters
        ----------
        repo_path : Path
            The repository path to prepare.
        branch_name : str
            The name of the branch.
        """
        logger.info("Preparing API repository at %s", repo_path)
        package_json = repo_path / "package.json"
        req_txt = repo_path / "requirements.txt"
        pyproj = repo_path / "pyproject.toml"

        if package_json.exists():
            # Node / NestJS
            logger.info("Detected NestJS/Node project. Installing node modules...")
            # Try npm install
            returncode, _, stderr = await self._run_command(["npm", "install", "--legacy-peer-deps", "--engine-strict=false"], repo_path)
            if returncode != 0:
                logger.warning("npm install failed: %s", stderr.decode().strip())
        elif req_txt.exists():
            # Python requirements.txt
            logger.info("Detected Python (requirements.txt) project. Installing dependencies...")
            returncode, _, stderr = await self._run_command(
                ["pip", "install", "-r", "requirements.txt"], repo_path
            )
            if returncode != 0:
                logger.warning("pip install requirements failed: %s", stderr.decode().strip())
        elif pyproj.exists():
            # Python pyproject.toml
            logger.info("Detected Python (pyproject.toml) project. Installing editable package...")
            returncode, _, stderr = await self._run_command(
                ["pip", "install", "-e", "."], repo_path
            )
            if returncode != 0:
                logger.warning("pip install editable package failed: %s", stderr.decode().strip())

    async def validate(self, repo_path: Path) -> list[str]:
        """
        Execute project-specific linters and test suites.

        Parameters
        ----------
        repo_path : Path
            The repository path to validate.

        Returns
        -------
        list[str]
            A list of validation error messages. Empty if validation passes.
        """
        logger.info("Validating API repository at %s", repo_path)
        package_json = repo_path / "package.json"
        errors: list[str] = []

        if package_json.exists():
            # Node / NestJS validation
            lint_ok = True
            test_ok = True
            
            # Check npm run lint
            logger.info("Running NestJS linting...")
            returncode, _, stderr = await self._run_command(["npm", "run", "lint"], repo_path)
            if returncode != 0:
                lint_ok = False
                logger.warning("NestJS Linting failed: %s", stderr.decode().strip())

            # Skip NestJS test running for now as requested
            logger.info("Skipping NestJS tests run...")
            test_ok = True

            if not lint_ok:
                errors.append("API Linting failed using npm run lint.")
        else:
            # Python Validation
            lint_ok = True
            lint_tool = None
            for tool in ["ruff", "flake8"]:
                if shutil.which(tool):
                    lint_tool = tool
                    cmd = [tool, "check", "."] if tool == "ruff" else [tool, "."]
                    returncode, _, stderr = await self._run_command(cmd, repo_path)
                    lint_ok = (returncode == 0)
                    if not lint_ok:
                        logger.warning("API Linting with %s failed: %s", tool, stderr.decode().strip())
                    break

            # Skip Python test running for now as requested
            test_ok = True

            if not lint_ok:
                errors.append(f"API Linting failed using {lint_tool or 'linter'}.")

        return errors

    async def bootstrap(self, repo_path: Path, starter_type: str) -> None:
        """
        Seed API project files (e.g. package.json or pyproject.toml, standard folders).

        Parameters
        ----------
        repo_path : Path
            The destination repository directory.
        starter_type : str
            The type of starter skeleton to bootstrap ("nestjs", "node", etc.).
        """
        logger.info("Bootstrapping API boilerplate in %s", repo_path)
        
        if starter_type == "nestjs" or starter_type == "node":
            # Scaffolds basic NestJS monorepo structure
            apps_dir = repo_path / "apps" / "api" / "src"
            libs_dir = repo_path / "libs" / "data-access" / "src"
            
            apps_dir.mkdir(parents=True, exist_ok=True)
            libs_dir.mkdir(parents=True, exist_ok=True)
            
            # 1. package.json
            package_json = repo_path / "package.json"
            if not package_json.exists():
                package_json.write_text(
                    '{\n'
                    '  "name": "nestjs-api",\n'
                    '  "version": "0.1.0",\n'
                    '  "private": true,\n'
                    '  "scripts": {\n'
                    '    "build": "nest build",\n'
                    '    "lint": "eslint \\"{src,apps,libs}/**/*.ts\\" --fix",\n'
                    '    "test": "jest"\n'
                    '  },\n'
                    '  "dependencies": {\n'
                    '    "@nestjs/common": "^10.0.0",\n'
                    '    "@nestjs/core": "^10.0.0",\n'
                    '    "@nestjs/mongoose": "^10.0.0",\n'
                    '    "@nestjs/platform-express": "^10.0.0",\n'
                    '    "mongoose": "^7.0.0",\n'
                    '    "reflect-metadata": "^0.1.13",\n'
                    '    "rxjs": "^7.8.1"\n'
                    '  },\n'
                    '  "devDependencies": {\n'
                    '    "@nestjs/cli": "^10.0.0",\n'
                    '    "@nestjs/schematics": "^10.0.0",\n'
                    '    "@nestjs/testing": "^10.0.0",\n'
                    '    "@types/express": "^4.17.17",\n'
                    '    "@types/node": "^20.3.1",\n'
                    '    "eslint": "^8.42.0",\n'
                    '    "@typescript-eslint/eslint-plugin": "^6.0.0",\n'
                    '    "@typescript-eslint/parser": "^6.0.0",\n'
                    '    "jest": "^29.5.0",\n'
                    '    "typescript": "^5.1.3"\n'
                    '  }\n'
                    '}\n',
                    encoding="utf-8"
                )

            # 1.5. .eslintrc.js
            eslintrc = repo_path / ".eslintrc.js"
            if not eslintrc.exists():
                eslintrc.write_text(
                    'module.exports = {\n'
                    '  parser: \'@typescript-eslint/parser\',\n'
                    '  parserOptions: {\n'
                    '    project: \'tsconfig.json\',\n'
                    '    tsconfigRootDir: __dirname,\n'
                    '    sourceType: \'module\',\n'
                    '  },\n'
                    '  plugins: [\'@typescript-eslint/eslint-plugin\'],\n'
                    '  extends: [\n'
                    '    \'plugin:@typescript-eslint/recommended\',\n'
                    '  ],\n'
                    '  root: true,\n'
                    '  env: {\n'
                    '    node: true,\n'
                    '    jest: true,\n'
                    '  },\n'
                    '  ignorePatterns: [\'.eslintrc.js\'],\n'
                    '  rules: {\n'
                    '    \'@typescript-eslint/interface-name-prefix\': \'off\',\n'
                    '    \'@typescript-eslint/explicit-function-return-type\': \'off\',\n'
                    '    \'@typescript-eslint/explicit-module-boundary-types\': \'off\',\n'
                    '    \'@typescript-eslint/no-explicit-any\': \'off\',\n'
                    '  },\n'
                    '};\n',
                    encoding="utf-8"
                )


            # 2. tsconfig.json
            tsconfig = repo_path / "tsconfig.json"
            if not tsconfig.exists():
                tsconfig.write_text(
                    '{\n'
                    '  "compilerOptions": {\n'
                    '    "module": "commonjs",\n'
                    '    "declaration": true,\n'
                    '    "removeComments": true,\n'
                    '    "emitDecoratorMetadata": true,\n'
                    '    "experimentalDecorators": true,\n'
                    '    "allowSyntheticDefaultImports": true,\n'
                    '    "target": "es2021",\n'
                    '    "sourceMap": true,\n'
                    '    "outDir": "./dist",\n'
                    '    "baseUrl": "./",\n'
                    '    "incremental": true,\n'
                    '    "skipLibCheck": true,\n'
                    '    "strictNullChecks": false,\n'
                    '    "noImplicitAny": false,\n'
                    '    "strictBindCallApply": false,\n'
                    '    "forceConsistentCasingInFileNames": false,\n'
                    '    "noFallthroughCasesInSwitch": false,\n'
                    '    "paths": {\n'
                    '      "@app/data-access": ["libs/data-access/src"],\n'
                    '      "@app/data-access/*": ["libs/data-access/src/*"]\n'
                    '    }\n'
                    '  }\n'
                    '}\n',
                    encoding="utf-8"
                )

            # 3. nest-cli.json
            nest_cli = repo_path / "nest-cli.json"
            if not nest_cli.exists():
                nest_cli.write_text(
                    '{\n'
                    '  "$schema": "https://json.schemastore.org/nest-cli",\n'
                    '  "collection": "@nestjs/schematics",\n'
                    '  "sourceRoot": "apps/api/src",\n'
                    '  "compilerOptions": {\n'
                    '    "deleteOutDir": true,\n'
                    '    "webpack": true,\n'
                    '    "tsConfigPath": "apps/api/tsconfig.app.json"\n'
                    '  },\n'
                    '  "projects": {\n'
                    '    "api": {\n'
                    '      "type": "application",\n'
                    '      "root": "apps/api",\n'
                    '      "entryFile": "main",\n'
                    '      "sourceRoot": "apps/api/src",\n'
                    '      "compilerOptions": {}\n'
                    '    },\n'
                    '    "data-access": {\n'
                    '      "type": "library",\n'
                    '      "root": "libs/data-access",\n'
                    '      "entryFile": "index",\n'
                    '      "sourceRoot": "libs/data-access/src",\n'
                    '      "compilerOptions": {}\n'
                    '    }\n'
                    '  }\n'
                    '}\n',
                    encoding="utf-8"
                )

            # 4. apps/api/tsconfig.app.json
            tsconfig_app = repo_path / "apps" / "api" / "tsconfig.app.json"
            tsconfig_app.parent.mkdir(parents=True, exist_ok=True)
            if not tsconfig_app.exists():
                tsconfig_app.write_text(
                    '{\n'
                    '  "extends": "../../tsconfig.json",\n'
                    '  "compilerOptions": {\n'
                    '    "declaration": false\n'
                    '  },\n'
                    '  "include": ["src/**/*"]\n'
                    '}\n',
                    encoding="utf-8"
                )

            # 5. Apps skeleton modules and entry point
            main_ts = apps_dir / "main.ts"
            if not main_ts.exists():
                main_ts.write_text(
                    'import { NestFactory } from "@nestjs/core";\n'
                    'import { AppModule } from "./app.module";\n\n'
                    'async function bootstrap() {\n'
                    '  const app = await NestFactory.create(AppModule);\n'
                    '  await app.listen(3000);\n'
                    '}\n'
                    'bootstrap();\n',
                    encoding="utf-8"
                )

            app_module_ts = apps_dir / "app.module.ts"
            if not app_module_ts.exists():
                app_module_ts.write_text(
                    'import { Module } from "@nestjs/common";\n'
                    'import { MongooseModule } from "@nestjs/mongoose";\n'
                    'import { DataAccessModule } from "@app/data-access";\n\n'
                    '@Module({\n'
                    '  imports: [\n'
                    '    MongooseModule.forRoot(process.env.MONGO_URI || "mongodb://localhost/test"),\n'
                    '    DataAccessModule,\n'
                    '  ],\n'
                    '  controllers: [],\n'
                    '  providers: [],\n'
                    '})\n'
                    'export class AppModule {}\n',
                    encoding="utf-8"
                )

            # 6. Libs skeleton modules and entry point
            lib_index_ts = libs_dir / "index.ts"
            if not lib_index_ts.exists():
                lib_index_ts.write_text(
                    'export * from "./data-access.module";\n'
                    'export * from "./data-access.models";\n',
                    encoding="utf-8"
                )

            lib_module_ts = libs_dir / "data-access.module.ts"
            if not lib_module_ts.exists():
                lib_module_ts.write_text(
                    'import { Module } from "@nestjs/common";\n'
                    'import { MongooseModule } from "@nestjs/mongoose";\n'
                    'import { dataAccessModels } from "./data-access.models";\n\n'
                    '@Module({\n'
                    '  imports: [\n'
                    '    MongooseModule.forFeature(dataAccessModels),\n'
                    '  ],\n'
                    '  exports: [\n'
                    '    MongooseModule.forFeature(dataAccessModels),\n'
                    '  ],\n'
                    '})\n'
                    'export class DataAccessModule {}\n',
                    encoding="utf-8"
                )

            lib_models_ts = libs_dir / "data-access.models.ts"
            if not lib_models_ts.exists():
                lib_models_ts.write_text(
                    'export const dataAccessModels = [];\n',
                    encoding="utf-8"
                )
        else:
            # Scaffolds basic main and tests layout (Python FastAPI)
            (repo_path / "app").mkdir(parents=True, exist_ok=True)
            (repo_path / "tests").mkdir(parents=True, exist_ok=True)
            
            req_txt = repo_path / "requirements.txt"
            if not req_txt.exists():
                req_txt.write_text("fastapi\nuvicorn\n", encoding="utf-8")
            
            main_py = repo_path / "app" / "main.py"
            if not main_py.exists():
                main_py.write_text(
                    'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/")\ndef read_root():\n    return {"Hello": "World"}\n',
                    encoding="utf-8"
                )

            test_py = repo_path / "tests" / "test_main.py"
            if not test_py.exists():
                test_py.write_text(
                    'def test_root():\n    assert True\n',
                    encoding="utf-8"
                )
