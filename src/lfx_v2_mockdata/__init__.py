# Copyright The Linux Foundation and each contributor to LFX.
# SPDX-License-Identifier: MIT

"""
Upload mock data script with YAML playbook support.

This script supports multiple workflow step types:
- 'http-request': HTTP requests with response handling
- 'nats-publish': NATS publish messages (fire-and-forget)
- 'nats-kv-put': NATS key-value store operations
- 'nats-request': NATS request-reply pattern with response storage

All step types support !ref JMESPath expressions for referencing previous
step responses and dynamic data binding.

Payload formats:
- For HTTP requests: use 'json' for JSON data, 'form' for multipart form,
  'raw' for raw bytes, or no body attribute for GET/HEAD requests
- For NATS steps: use 'json' for JSON data, 'raw' for raw UTF8 strings,
  or omit both to send an empty payload

"""

import argparse
import asyncio
import contextvars
import datetime
import glob
import json
import os
import re
import sys
import time
from base64 import b64encode
from collections import OrderedDict
from http import HTTPMethod
from typing import Any

import jmespath
import jwt
import nats
import requests
import structlog
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv
from faker import Faker
from jinja2 import Environment, FileSystemLoader, select_autoescape
from nats.aio.client import Client as NatsClient
from nats.errors import TimeoutError
from nats.js import JetStreamContext
from pydantic import BaseModel

from custom_logging import setup_logging

load_dotenv()

fake = Faker()

# Number of iterations *per playbook* to re-attempt the entire run (in order to
# resolve !ref dependencies) before giving up.
RETRIES_PER_PLAYBOOK = 3


class UploadMockDataArgs(BaseModel):
    """Arguments for upload_mock_data CLI."""

    template_dirs: list[str]
    dump: bool = False
    dump_json: bool = False
    dry_run: bool = False
    upload: bool = False
    force: bool = False
    jwt_rsa_secret: str | None = None
    jwt_key_id: str | None = None


jmespath_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "jmespath_context"
)
jinja_env: contextvars.ContextVar[Environment] = contextvars.ContextVar("jinja_env")
args: contextvars.ContextVar[UploadMockDataArgs] = contextvars.ContextVar("args")
retries_remaining: contextvars.ContextVar[int] = contextvars.ContextVar(
    "retries_remaining"
)

# NATS connection variables.
nats_client: None | NatsClient = None
jetstream_client: None | JetStreamContext = None

# JWT caching variables.
_jwt_cache: dict[str, tuple[str, float]] = {}  # Cache JWT tokens with expiry.
_jwks_kid_cache: str = ""  # Cache JWKS key ID.
JWT_CACHE_TTL = 240  # Cache JWT tokens for 4 minutes (1 minute before expiry).

# NATS configuration.
NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")
WAIT_TIMEOUT = 10  # seconds

setup_logging()
logger = structlog.get_logger()


def slug_from_project_name(project_name: str) -> str:
    """Generate a sanitized slug from a project name.

    The slug is based on the project name lowercased, with non-alphanumeric
    characters replaced with hyphens, consecutive hyphens replaced with a
    single hyphen, and leading and trailing hyphens stripped.
    """
    if not project_name:
        return ""

    # Convert to lowercase.
    slug = project_name.lower()

    # Replace non-alphanumeric characters with hyphens.
    slug = re.sub(r"[^a-z0-9]+", "-", slug)

    # Replace consecutive hyphens with a single hyphen.
    slug = re.sub(r"-+", "-", slug)

    # Strip leading and trailing hyphens.
    slug = slug.strip("-")

    return slug


class JMESPath(yaml.YAMLObject):
    """JMESPath represents a parsed !ref YAML tag.

    The !ref tag is a JMESPath expression which is late-evaluated only when the
    object is serialized to JSON. This allows the expression to point to output
    values that don't exist in the source YAML.
    """

    def __init__(self, expression):
        self.expression = expression

    def __repr__(self):
        return f"JMESPath({repr(self.expression)})"

    # All the following methods evaluate the path and then pass through the
    # same, allowing the object to typically masquerade as the correct type
    # when evaluated.
    def __str__(self):
        return str(self.evaluate())

    def __int__(self):
        return int(self.evaluate())

    def __float__(self):
        return float(self.evaluate())

    def __iter__(self):
        return iter(self.evaluate())

    def __getitem__(self, name):
        return self.evaluate()[name]

    def __len__(self):
        return len(self.evaluate())

    def keys(self, *args):
        return self.evaluate().keys(*args)

    def evaluate(self):
        """Return the node that the JMESPath expression evaluates to.

        This allows us to late-bind the JMESPath expression during JSON
        serialization or value casting, rather than during the YAML parsing,
        because the entire document will not have been built yet. This must be
        run from within a Context that has a reference to the entire data tree.

        This does not explicitly check for circular references, but json.dumps
        will raise "ValueError: Circular reference detected" if one is created.
        """
        data_context = jmespath_context.get()
        # Attempt to evaluate expression against data context.
        value = jmespath.search(self.expression, data_context)
        if value is None:
            raise AttributeError(
                f"JMESPath expression '{self.expression}' not found in data"
            )
        return value


class JMESPathSubstitution(yaml.YAMLObject):
    """JMESPathSubstitution represents a parsed !sub YAML tag.

    The !sub tag is a template string with ${...} placeholders that contain
    JMESPath expressions. These are late-evaluated during JSON serialization,
    allowing interpolation of values that don't exist during YAML parsing.

    Example:
        !sub "project:${global_groups_root_lookup.steps[0]._response}"
    """

    def __init__(self, template):
        self.template = template

    def __repr__(self):
        return f"JMESPathSubstitution({repr(self.template)})"

    # All the following methods evaluate the template and then pass through the
    # same, allowing the object to typically masquerade as the correct type
    # when evaluated.
    def __str__(self):
        return str(self.evaluate())

    def __int__(self):
        return int(self.evaluate())

    def __float__(self):
        return float(self.evaluate())

    def __iter__(self):
        return iter(self.evaluate())

    def __getitem__(self, name):
        return self.evaluate()[name]

    def __len__(self):
        return len(self.evaluate())

    def keys(self, *args):
        return self.evaluate().keys(*args)

    def evaluate(self):
        """Return the template string with ${...} placeholders substituted.

        This allows us to late-bind JMESPath expressions within string templates
        during JSON serialization or value casting, rather than during the YAML
        parsing, because the entire document will not have been built yet. This
        must be run from within a Context that has a reference to the entire
        data tree.

        This does not explicitly check for circular references, but json.dumps
        will raise "ValueError: Circular reference detected" if one is created.
        """
        data_context = jmespath_context.get()

        def replace_placeholder(match):
            expression = match.group(1)
            # Attempt to evaluate expression against data context.
            value = jmespath.search(expression, data_context)
            if value is None:
                raise AttributeError(
                    f"JMESPath expression '{expression}' not found in data"
                )
            return str(value)

        # Find and replace all ${...} patterns with their evaluated values.
        result = re.sub(r"\$\{([^}]+)\}", replace_placeholder, self.template)
        return result


class JWTGenerator(yaml.YAMLObject):
    """JWTGenerator represents a parsed !jwt YAML tag.

    The !jwt tag generates a JWT token with the specified claims.
    Arguments are passed as key=value pairs separated by commas.

    Example:
        !jwt aud=lfx-v2-project-service,principal=clients@m2m_helper,\\
             email=test@example.com
    """

    def __init__(self, args_string):
        self.args_string = args_string
        self.parsed_args = self._parse_args(args_string)

    def __repr__(self):
        return f"JWTGenerator({repr(self.args_string)})"

    def __str__(self):
        return str(self.generate_jwt())

    def _parse_args(self, args_string: str) -> dict[str, str]:
        """Parse key=value,key=value arguments into a dictionary."""
        args_dict: dict[str, str] = {}
        if not args_string:
            return args_dict

        # Split by comma and parse key=value pairs.
        for pair in args_string.split(","):
            pair = pair.strip()
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            args_dict[key.strip()] = value.strip()
        return args_dict

    def generate_jwt(self) -> str:
        """Generate a JWT token based on the provided arguments."""
        cli_args = args.get()

        # Check if we have the RSA secret.
        if not cli_args.jwt_rsa_secret:
            raise ValueError("JWT RSA secret not provided via --jwt-rsa-secret")

        # Required arguments.
        audience = self.parsed_args.get("aud")
        principal = self.parsed_args.get("principal")

        if not audience:
            raise ValueError("JWT 'aud' (audience) argument is required")
        if not principal:
            raise ValueError("JWT 'principal' argument is required")

        # Optional email.
        email = self.parsed_args.get("email")

        # Create cache key based on the JWT arguments.
        bearer = self.parsed_args.get("bearer")
        cache_key = f"{audience}|{principal}|{email or ''}|{bearer or ''}"

        # Check if we have a cached JWT that's still valid.
        now = time.time()
        if cache_key in _jwt_cache:
            cached_token, cache_time = _jwt_cache[cache_key]
            if now - cache_time < JWT_CACHE_TTL:
                return cached_token

        # Get or fetch the key ID.
        key_id = self._get_key_id(cli_args)

        # Create JWT payload.
        now_int = int(now)
        payload = {
            "aud": audience,
            "iss": "heimdall",
            "sub": principal.replace("clients@", ""),  # Remove clients@ prefix.
            "principal": principal,
            "exp": now_int + 300,  # 5 minutes expiry.
            "nbf": now_int,  # Valid from now.
            "jti": fake.uuid4(),  # Unique JWT ID.
        }

        # Add email to payload if provided.
        if email:
            payload["email"] = email

        # Load the RSA private key.
        try:
            loaded_key = serialization.load_pem_private_key(
                cli_args.jwt_rsa_secret.encode(), password=None
            )
            # Ensure we have an RSA private key for PS256 algorithm.
            if not isinstance(loaded_key, rsa.RSAPrivateKey):
                raise ValueError(
                    "JWT signing requires an RSA private key for PS256 algorithm"
                )
            private_key = loaded_key
        except Exception as e:
            raise ValueError(f"Failed to load RSA private key: {e}") from e

        # Generate the JWT.
        token = jwt.encode(
            payload,
            private_key,
            algorithm="PS256",
            headers={"kid": key_id},
        )

        if bearer and bearer.lower() in ("true", "t", "1", "yes", "y"):
            token = f"Bearer {token}"

        # Cache the generated token.
        _jwt_cache[cache_key] = (token, now)

        return token

    def _get_key_id(self, cli_args) -> str:
        """Get the JWT key ID from command line argument or JWKS endpoint."""
        global _jwks_kid_cache

        # Use command line argument if provided.
        if cli_args.jwt_key_id:
            return cli_args.jwt_key_id

        # Check if we have a cached JWKS key ID.
        if _jwks_kid_cache != "":
            return _jwks_kid_cache

        # Fetch from Heimdall JWKS endpoint.
        jwks_url = (
            "http://lfx-platform-heimdall.lfx.svc.cluster.local:4457/.well-known/jwks"
        )
        try:
            response = requests.get(jwks_url, timeout=10)
            response.raise_for_status()
            jwks_data = response.json()

            if "keys" not in jwks_data or not jwks_data["keys"]:
                raise ValueError("No keys found in JWKS response")

            key_id = jwks_data["keys"][0].get("kid")
            if not key_id:
                raise ValueError("No key ID found in first JWKS key")

            # Cache the fetched key ID.
            _jwks_kid_cache = key_id
            return key_id
        except Exception as e:
            raise ValueError(
                f"Failed to fetch key ID from JWKS endpoint: {e}. "
                "Consider using --jwt-key-id argument."
            ) from e


class JMESPathEncoder(json.JSONEncoder):
    """Extend the default JSON encoder for JMESPath macros.

    Supports JMESPath (!ref), JMESPathSubstitution (!sub), and JWTGenerator
    (!jwt) macros.
    """

    def default(self, obj):
        if isinstance(obj, JMESPath):
            return obj.evaluate()
        if isinstance(obj, JMESPathSubstitution):
            return obj.evaluate()
        if isinstance(obj, JWTGenerator):
            return obj.generate_jwt()
        # Handle all other types (or raise a TypeError).
        return super().default(obj)


class HttpRequestPlaybookParams(BaseModel):
    """Parameters for a playbook of type 'http-request'."""

    url: str
    method: HTTPMethod
    headers: dict[str, str] = {}
    params: dict[str, str] = {}


class NatsPublishPlaybookParams(BaseModel):
    """Parameters for a playbook of type 'nats-publish'."""

    subject: str


class NatsKvPutPlaybookParams(BaseModel):
    """Parameters for a playbook of type 'nats-kv-put'."""

    bucket: str
    key: str


class NatsRequestPlaybookParams(BaseModel):
    """Parameters for a playbook of type 'nats-request'."""

    subject: str
    timeout: int = WAIT_TIMEOUT


def yaml_ref(loader, node):
    """Convert !ref YAML tag to JMESPath object.

    This function is registered with the YAML loader via add_constructor().
    """
    return JMESPath(node.value)


def ref_yaml(dumper, data):
    """Represent JMESPath object as a !ref YAML tag.

    This function is registered with the YAML dumper via add_representer().
    """
    return dumper.represent_scalar("!ref", data.expression)


def yaml_sub(loader, node):
    """Convert !sub YAML tag to JMESPathSubstitution object.

    This function is registered with the YAML loader via add_constructor().
    """
    return JMESPathSubstitution(node.value)


def sub_yaml(dumper, data):
    """Represent JMESPathSubstitution object as a !sub YAML tag.

    This function is registered with the YAML dumper via add_representer().
    """
    return dumper.represent_scalar("!sub", data.template)


def yaml_include(loader, node):
    """Convert !include YAML tag to Jinja2 render and YAML parse.

    This function is registered with the YAML loader via add_constructor().
    """
    env = jinja_env.get()
    logger.info(
        "Loading included template",
        template_dir=env.loader.searchpath[0],
        yaml_file=node.value,
    )
    template = env.get_template(node.value)
    out_data = template.render()
    return yaml.safe_load(out_data)


def yaml_jwt(loader, node):
    """Convert !jwt YAML tag to JWTGenerator object.

    This function is registered with the YAML loader via add_constructor().
    """
    return JWTGenerator(node.value)


def jwt_yaml(dumper, data):
    """Represent JWTGenerator object as a !jwt YAML tag.

    This function is registered with the YAML dumper via add_representer().
    """
    return dumper.represent_scalar("!jwt", data.args_string)


def yaml_render(template_dir, yaml_file):
    """Setup Jinja2 and render and parse a YAML file."""
    logger.info("Loading template", template_dir=template_dir, yaml_file=yaml_file)
    # Check if we have already created a sandbox Jinja2 environment for this
    # context/directory.
    env = jinja_env.get(None)
    if env is None:
        # Create an environment restricted to the passed template directory.
        env = Environment(
            loader=FileSystemLoader(searchpath=template_dir),
            autoescape=select_autoescape(
                default_for_string=True,
                default=True,
            ),
        )
        # Add helper functions to the Jinja2 environment.
        env.globals["b64encode"] = lambda s: b64encode(s).decode()
        env.globals["environ"] = dict(os.environ)
        env.globals["fake"] = fake
        env.globals["timedelta"] = datetime.timedelta
        env.globals["now_z"] = (
            lambda: datetime.datetime.now(datetime.UTC)
            .isoformat("T")
            .replace("+00:00", "Z")
        )
        env.globals["slug_from_project_name"] = slug_from_project_name
        # Store the environment in the context for use by the !include
        # constructor/macro and remaining YAML files in this context/directory.
        jinja_env.set(env)
    template = env.get_template(yaml_file)
    out_data = template.render()
    return yaml.safe_load(out_data)


def main() -> None:
    """Implement command-line interface."""
    # Parse CLI arguments.
    cli_args = parse_args()
    # Store the argparse namespace into the context for use in nested
    # functions.
    args.set(cli_args)
    # Load and parse the requested template directories.
    data = merge_and_preprocess_yaml_dirs(cli_args.template_dirs)
    # Set the context for JMESPath expression evaluation to the data returned
    # from merge_and_preprocess_yaml_dirs.
    jmespath_context.set(data)
    # Conditionally dump data to stdout.
    if cli_args.dump:
        # PyYAML outputs OrderedDicts as arrays, but casting to a dict and
        # disabling sort_keys seems to work as expected (outputs as a map and
        # retains order). Note that the YAML dump evaluates `!import` but does
        # NOT evaluate the `!ref` JMESPath expressions.
        sys.stdout.write(yaml.dump(dict(data), sort_keys=False))
    if cli_args.dump_json:
        try:
            # json.dumps preserves order while outputting an OrderedDict as an
            # ordinary map. The JSON dump evaluates all `!ref` JMESPath
            # expressions, unlike the YAML dump.
            print(json.dumps(data, cls=JMESPathEncoder, separators=(",", ":")))
        except AttributeError as e:
            logger.error("Error dumping JSON", error=str(e))
    # Return early if we are only dumping data.
    if (cli_args.dump or cli_args.dump_json) and not cli_args.upload:
        return
    # Run playbooks to upload mock data.
    try:
        asyncio.run(run_playbooks_async(data))
    except json.decoder.JSONDecodeError as e:
        logger.error("Failed to parse response as JSON", error=str(e))
    except requests.exceptions.RequestException as e:
        logger.error("Request failed", error=str(e))
    except AttributeError as e:
        logger.error("Error processing playbook", error=str(e))


def merge_and_preprocess_yaml_dirs(template_dirs: list[str]) -> OrderedDict:
    """Step over each template directory that is part of this run.

    This function scans for YAML files and loads them individually.
    """
    data: OrderedDict[str, Any] = OrderedDict()
    for template_dir in template_dirs:
        # Create a subcontext for this template_dir, which is used as a sandbox
        # for the `!include` constructor's Jinja environment.
        ctx = contextvars.copy_context()

        # Find all YAML files in the template directory.
        yaml_patterns = [
            os.path.join(template_dir, "*.yaml"),
            os.path.join(template_dir, "*.yml"),
        ]

        yaml_files = []
        for pattern in yaml_patterns:
            yaml_files.extend(glob.glob(pattern))

        # Process each YAML file in Unix order (numerals, then uppercase, then
        # lowercase).
        for yaml_file in sorted(yaml_files):
            # Run the template evaluation in the context.
            new_data = ctx.run(yaml_render, template_dir, os.path.basename(yaml_file))
            # Warn if new_data is not a dictionary.
            if not isinstance(new_data, dict):
                logger.warning(
                    "YAML file did not parse to a dictionary",
                    template_dir=template_dir,
                    yaml_file=yaml_file,
                )
                continue
            # Warn if any playbook names (keys in the dictionary) would collide.
            # (use set intersection to find any duplicates)
            duplicate_keys = set(data.keys()).intersection(new_data.keys())
            if duplicate_keys:
                # Log a warning and skip the entire file.
                logger.warning(
                    "Duplicate playbook names found; skipping file",
                    template_dir=template_dir,
                    yaml_file=yaml_file,
                    duplicate_playbooks=list(duplicate_keys),
                )
                continue
            # Increment our global retry counter for this playbook.
            retries_remaining.set(retries_remaining.get() + RETRIES_PER_PLAYBOOK)
            # Merge the new data into the overall data dictionary.
            data.update(new_data)
    return data


async def run_playbooks_async(data: dict) -> None:
    """Async wrapper for running playbooks with NATS support."""
    try:
        await run_playbooks(data)
    finally:
        # Only cleanup if NATS was actually connected.
        if nats_client is not None:
            await cleanup_nats_connection()


async def initialize_nats_connection() -> None:
    """Initialize NATS client connection if not already connected."""
    global nats_client, jetstream_client
    if nats_client is None:
        try:
            nats_client = await nats.connect(NATS_URL, max_reconnect_attempts=3)
            jetstream_client = nats_client.jetstream()
            logger.info("Connected to NATS", url=NATS_URL)
        except Exception as e:
            logger.error("Failed to connect to NATS", error=str(e))
            raise


async def cleanup_nats_connection() -> None:
    """Clean up NATS client connection."""
    global nats_client, jetstream_client
    if nats_client is not None:
        await nats_client.close()
        nats_client = None
        jetstream_client = None
        logger.info("Disconnected from NATS")


async def run_playbooks(data: dict) -> None:
    cli_args = args.get()
    while retries_remaining.get() >= 0:
        for name, playbook in data.items():
            if "type" not in playbook:
                if cli_args.force:
                    logger.error("Playbook missing type", playbook=name)
                    continue
                raise AttributeError(f"Playbook '{name}' missing type")
            if playbook["type"] == "http-request":
                run_http_request_playbook(name, playbook)
            elif playbook["type"] == "nats-publish":
                await run_nats_publish_playbook(name, playbook)
            elif playbook["type"] == "nats-kv-put":
                await run_nats_kv_put_playbook(name, playbook)
            elif playbook["type"] == "nats-request":
                await run_nats_request_playbook(name, playbook)
            else:
                if cli_args.force:
                    logger.error("Playbook has unknown type", playbook=name)
                    continue
                raise AttributeError(f"Playbook '{name}' has unknown type")
        retries_remaining.set(retries_remaining.get() - 1)


def run_http_request_playbook(name: str, playbook: dict) -> None:
    """Run a playbook of type 'http-request'."""
    cli_args = args.get()
    if "params" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing params", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing params")
    params = HttpRequestPlaybookParams.model_validate_json(
        json.dumps(
            playbook["params"],
            cls=JMESPathEncoder,
            separators=(",", ":"),
        )
    )
    if "steps" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing steps", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing steps")
    for step_payload in playbook["steps"]:
        if "_response" in step_payload:
            # Skip steps that have already been run.
            continue

        # Determine payload type and prepare data.
        request_data = None
        if params.method in [HTTPMethod.POST, HTTPMethod.PUT, HTTPMethod.PATCH]:
            try:
                if "json" in step_payload:
                    params.headers["content-type"] = "application/json"
                    request_data = json.dumps(
                        step_payload["json"],
                        cls=JMESPathEncoder,
                        separators=(",", ":"),
                    )
                elif "form" in step_payload:
                    processed_data = json.dumps(
                        step_payload["form"],
                        cls=JMESPathEncoder,
                        separators=(",", ":"),
                    )
                    # Convert back to a dict; requests will handle multipart
                    # encoding.
                    request_data = json.loads(processed_data)
            except AttributeError as e:
                if cli_args.dry_run:
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        step_payload["_response"] = {}
                        continue
                    else:
                        raise
                else:
                    if retries_remaining.get() > 0:
                        continue
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        continue
                    raise
            if request_data is None and "raw" in step_payload:
                if isinstance(step_payload["raw"], str):
                    request_data = step_payload["raw"]
                else:
                    request_data = str(step_payload["raw"])

        if cli_args.dry_run:
            # If we're in a dry-run, don't actually run the request.
            return

        logger.info(
            "Running step",
            playbook=name,
            method=params.method,
            url=params.url,
            data=request_data,
        )

        try:
            response = requests.request(
                **params.model_dump(),
                data=request_data,
            )
            response.raise_for_status()
            # Store the response in the playbook for future reference.
        except requests.exceptions.RequestException as e:
            if cli_args.force:
                logger.error("Request failed", error=str(e), playbook=name)
                # Add a placeholder response to prevent re-running.
                step_payload["_response"] = {}
                continue
            raise
        try:
            r_dict = response.json()
            step_payload["_response"] = r_dict
        except json.decoder.JSONDecodeError as e:
            if cli_args.force:
                logger.error(
                    "Failed to parse response as JSON", error=str(e), playbook=name
                )
                # Add a placeholder response to prevent re-running.
                step_payload["_response"] = {}
                continue
            raise


async def run_nats_publish_playbook(name: str, playbook: dict) -> None:
    """Run a playbook of type 'nats-publish'."""
    cli_args = args.get()

    # Initialize NATS connection if needed.
    await initialize_nats_connection()

    if nats_client is None:
        if cli_args.force:
            logger.error("NATS client not connected", playbook=name)
            return
        raise AttributeError("NATS client not connected")

    if "params" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing params", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing params")

    params = NatsPublishPlaybookParams.model_validate_json(
        json.dumps(
            playbook["params"],
            cls=JMESPathEncoder,
            separators=(",", ":"),
        )
    )

    if "steps" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing steps", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing steps")

    for step_payload in playbook["steps"]:
        if "_response" in step_payload:
            # Skip steps that have already been run.
            continue

        # Determine payload type and prepare data.
        if "json" in step_payload:
            try:
                data = json.dumps(
                    step_payload["json"],
                    cls=JMESPathEncoder,
                    separators=(",", ":"),
                ).encode()
            except AttributeError as e:
                if cli_args.dry_run:
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        step_payload["_response"] = {}
                        continue
                    else:
                        raise
                else:
                    if retries_remaining.get() > 0:
                        continue
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        continue
                    raise
        elif "raw" in step_payload:
            if isinstance(step_payload["raw"], str):
                data = step_payload["raw"].encode("utf-8")
            else:
                data = str(step_payload["raw"]).encode("utf-8")
        else:
            # Send empty payload if neither json nor raw specified
            data = b""

        if cli_args.dry_run:
            # If we're in a dry-run, don't actually run the request.
            step_payload["_response"] = {}
            continue

        logger.info(
            "Publishing NATS message",
            playbook=name,
            subject=params.subject,
            data_length=len(data),
        )

        try:
            await nats_client.publish(params.subject, data)
            # NATS publish doesn't return a response, so we create an empty one.
            step_payload["_response"] = {}
        except Exception as e:
            if cli_args.force:
                logger.error("NATS publish failed", error=str(e), playbook=name)
                step_payload["_response"] = {}
                continue
            raise


async def run_nats_kv_put_playbook(name: str, playbook: dict) -> None:
    """Run a playbook of type 'nats-kv-put'."""
    cli_args = args.get()

    # Initialize NATS connection if needed.
    await initialize_nats_connection()

    if jetstream_client is None:
        if cli_args.force:
            logger.error("NATS JetStream client not connected", playbook=name)
            return
        raise AttributeError("NATS JetStream client not connected")

    if "params" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing params", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing params")

    params = NatsKvPutPlaybookParams.model_validate_json(
        json.dumps(
            playbook["params"],
            cls=JMESPathEncoder,
            separators=(",", ":"),
        )
    )

    # Get or create the KV bucket.
    try:
        kv_client = await jetstream_client.key_value(params.bucket)
    except Exception as e:
        if cli_args.force:
            logger.error(
                "Failed to access KV bucket",
                bucket=params.bucket,
                error=str(e),
                playbook=name,
            )
            return
        raise

    if "steps" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing steps", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing steps")

    for step_payload in playbook["steps"]:
        if "_response" in step_payload:
            # Skip steps that have already been run.
            continue

        # Determine payload type and prepare data.
        if "json" in step_payload:
            try:
                data = json.dumps(
                    step_payload["json"],
                    cls=JMESPathEncoder,
                    separators=(",", ":"),
                ).encode()
            except AttributeError as e:
                if cli_args.dry_run:
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        step_payload["_response"] = {}
                        continue
                    else:
                        raise
                else:
                    if retries_remaining.get() > 0:
                        continue
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        continue
                    raise
        elif "raw" in step_payload:
            if isinstance(step_payload["raw"], str):
                data = step_payload["raw"].encode("utf-8")
            else:
                data = str(step_payload["raw"]).encode("utf-8")
        else:
            # Send empty payload if neither json nor raw specified
            data = b""

        if cli_args.dry_run:
            # If we're in a dry-run, don't actually run the request.
            step_payload["_response"] = {}
            continue

        logger.info(
            "Putting NATS KV entry",
            playbook=name,
            key=params.key,
            data_length=len(data),
        )

        try:
            await kv_client.put(params.key, data)
            # NATS KV put doesn't return a response, so we create an empty one.
            step_payload["_response"] = {}
        except Exception as e:
            if cli_args.force:
                logger.error("NATS KV put failed", error=str(e), playbook=name)
                step_payload["_response"] = {}
                continue
            raise


async def run_nats_request_playbook(name: str, playbook: dict) -> None:
    """Run a playbook of type 'nats-request'."""
    cli_args = args.get()

    # Initialize NATS connection if needed.
    await initialize_nats_connection()

    if nats_client is None:
        if cli_args.force:
            logger.error("NATS client not connected", playbook=name)
            return
        raise AttributeError("NATS client not connected")

    if "params" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing params", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing params")

    params = NatsRequestPlaybookParams.model_validate_json(
        json.dumps(
            playbook["params"],
            cls=JMESPathEncoder,
            separators=(",", ":"),
        )
    )

    if "steps" not in playbook:
        if cli_args.force:
            logger.error("Playbook missing steps", playbook=name)
            return
        raise AttributeError(f"Playbook '{name}' missing steps")

    for step_payload in playbook["steps"]:
        if "_response" in step_payload:
            # Skip steps that have already been run.
            continue

        # Determine payload type and prepare data.
        if "json" in step_payload:
            try:
                data = json.dumps(
                    step_payload["json"],
                    cls=JMESPathEncoder,
                    separators=(",", ":"),
                ).encode()
            except AttributeError as e:
                if cli_args.dry_run:
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        step_payload["_response"] = {}
                        continue
                    else:
                        raise
                else:
                    if retries_remaining.get() > 0:
                        continue
                    if cli_args.force:
                        logger.error(
                            "Error processing playbook", error=str(e), playbook=name
                        )
                        continue
                    raise
        elif "raw" in step_payload:
            if isinstance(step_payload["raw"], str):
                data = step_payload["raw"].encode("utf-8")
            else:
                data = str(step_payload["raw"]).encode("utf-8")
        else:
            # Send empty payload if neither json nor raw specified
            data = b""

        if cli_args.dry_run:
            # If we're in a dry-run, don't actually run the request.
            step_payload["_response"] = {}
            continue

        logger.info(
            "Sending NATS request",
            playbook=name,
            subject=params.subject,
            data_length=len(data),
            timeout=params.timeout,
        )

        try:
            response = await nats_client.request(
                params.subject, data, timeout=params.timeout
            )
            # Parse the response data and store it.
            try:
                response_data = json.loads(response.data.decode())
                step_payload["_response"] = response_data
            except json.JSONDecodeError:
                # If response is not JSON, store it as a string.
                step_payload["_response"] = response.data.decode()
        except TimeoutError as e:
            if cli_args.force:
                logger.error("NATS request timeout", error=str(e), playbook=name)
                step_payload["_response"] = {}
                continue
            raise
        except Exception as e:
            if cli_args.force:
                logger.error("NATS request failed", error=str(e), playbook=name)
                step_payload["_response"] = {}
                continue
            raise


def parse_args() -> UploadMockDataArgs:
    """Handle argument parsing for CLI invocations."""
    parser = argparse.ArgumentParser(description="Upload mock data to endpoints")
    parser.add_argument(
        "-t",
        "--template-dir",
        dest="template_dirs",
        nargs="+",
        required=True,
        help="path(s) to directory of YAML playbooks",
    )
    dumper_group = parser.add_mutually_exclusive_group()
    dumper_group.add_argument(
        "--dump",
        action="store_true",
        help="dump the parsed templates as YAML to stdout (no !ref expansion)",
    )
    dumper_group.add_argument(
        "--dump-json",
        action="store_true",
        help="dump the parsed templates as JSON to stdout (with !ref expansion)",
    )
    dry_run_group = parser.add_mutually_exclusive_group()
    dry_run_group.add_argument(
        "--dry-run",
        action="store_true",
        help="do not upload any data to endpoints",
    )
    dry_run_group.add_argument(
        "--upload",
        action="store_true",
        help="upload to endpoints even when dumping",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="keep running steps after a failure",
    )
    parser.add_argument(
        "--jwt-rsa-secret",
        dest="jwt_rsa_secret",
        help="RSA private key in PEM format for JWT signing",
    )
    parser.add_argument(
        "--jwt-key-id",
        dest="jwt_key_id",
        help=(
            "JWT key ID (kid) for JWT header. If not provided, will "
            "fetch from Heimdall JWKS endpoint"
        ),
    )
    # Parse arguments and convert to Pydantic model.
    parsed_args = parser.parse_args()
    return UploadMockDataArgs(
        template_dirs=parsed_args.template_dirs,
        dump=parsed_args.dump,
        dump_json=parsed_args.dump_json,
        dry_run=parsed_args.dry_run,
        upload=parsed_args.upload,
        force=parsed_args.force,
        jwt_rsa_secret=parsed_args.jwt_rsa_secret,
        jwt_key_id=parsed_args.jwt_key_id,
    )


yaml.SafeLoader.add_constructor("!include", yaml_include)
yaml.SafeLoader.add_constructor("!ref", yaml_ref)
yaml.SafeLoader.add_constructor("!sub", yaml_sub)
yaml.SafeLoader.add_constructor("!jwt", yaml_jwt)
yaml.add_representer(JMESPath, ref_yaml)
yaml.add_representer(JMESPathSubstitution, sub_yaml)
yaml.add_representer(JWTGenerator, jwt_yaml)

jmespath_context.set({})
args.set(UploadMockDataArgs(template_dirs=[]))
retries_remaining.set(0)

if __name__ == "__main__":
    main()
