"""Configuration loader - merges .env secrets with settings.yaml business rules."""

import os
import logging
import logging.config
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Project root directory
ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "config"


def load_settings() -> dict:
    """Load and merge configuration from .env and settings.yaml."""
    # Load .env file (secrets)
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        logger.warning("No .env file found at %s - using environment variables only", env_path)

    # Load settings.yaml (business rules)
    settings_path = CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    with open(settings_path, "r") as f:
        settings = yaml.safe_load(f)

    return settings


def get_db_connection_string() -> str:
    """Build SQL Server connection string from environment variables."""
    driver = os.getenv("DB_DRIVER", "{ODBC Driver 17 for SQL Server}")
    server = os.getenv("DB_SERVER", "localhost")
    port = os.getenv("DB_PORT", "1433")
    database = os.getenv("DB_NAME", "")
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")

    return (
        f"DRIVER={driver};"
        f"SERVER={server},{port};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
    )


def get_gocanvas_credentials() -> dict:
    """Get GoCanvas API credentials from environment."""
    return {
        "username": os.getenv("GOCANVAS_USERNAME", ""),
        "api_key": os.getenv("GOCANVAS_API_KEY", ""),
    }


def get_anthropic_api_key() -> str:
    """Get Anthropic API key from environment."""
    return os.getenv("ANTHROPIC_API_KEY", "")


def get_smtp_config() -> dict:
    """Get SMTP email configuration from environment."""
    return {
        "host": os.getenv("SMTP_HOST", "smtp.office365.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        "from_addr": os.getenv("SMTP_FROM", ""),
    }


def get_flask_config() -> dict:
    """Get Flask server configuration from environment."""
    return {
        "host": os.getenv("FLASK_HOST", "0.0.0.0"),
        "port": int(os.getenv("FLASK_PORT", "5000")),
        "secret_key": os.getenv("FLASK_SECRET_KEY", "change-me"),
    }


def setup_logging():
    """Configure logging from logging.yaml."""
    logging_config_path = CONFIG_DIR / "logging.yaml"

    # Ensure log directory exists
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    if logging_config_path.exists():
        with open(logging_config_path, "r") as f:
            log_config = yaml.safe_load(f)
        # Fix relative log file path
        if "handlers" in log_config and "file" in log_config["handlers"]:
            log_config["handlers"]["file"]["filename"] = str(log_dir / "app.log")
        logging.config.dictConfig(log_config)
    else:
        logging.basicConfig(level=logging.INFO)
        logger.warning("No logging.yaml found, using basic config")
