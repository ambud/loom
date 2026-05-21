"""Safety utilities for identifying sensitive files and secrets."""

import os
import fnmatch

# List of patterns that should never be read or committed
# Includes SSH keys, env files, credentials, and common secret files
SENSITIVE_PATTERNS = [
    # SSH and identity
    "**/id_rsa*", "**/id_dsa*", "**/id_ed25519*", "**/id_ecdsa*",
    "**/authorized_keys", "**/known_hosts",
    # Credentials and secrets
    "**/.env*", "**/.secret*", "**/credentials.json", "**/token.json",
    "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx",
    "**/passwd", "**/shadow",
    # Cloud and API
    "**/.aws/credentials", "**/.gcloud/*", "**/firebase.json",
]

def is_sensitive(filepath: str) -> bool:
    """Check if a file path matches any sensitive patterns."""
    # Convert to lowercase for case-insensitive matching
    base = filepath.lower()
    filename = os.path.basename(base)
    
    for pattern in SENSITIVE_PATTERNS:
        p = pattern.lower()
        # Direct match on filename if pattern is a simple filename
        if not "/" in p:
            if fnmatch.fnmatch(filename, p):
                return True
        # Path match if pattern contains / or **
        else:
            # Handle **/ by also checking if the filename matches the part after **/
            if p.startswith("**/"):
                sub_p = p[3:]
                if not "/" in sub_p and fnmatch.fnmatch(filename, sub_p):
                    return True
            if fnmatch.fnmatch(base, p):
                return True
    return False

def check_sensitive_access(filepath: str) -> str | None:
    """Return an error message if the file is sensitive, else None."""
    if is_sensitive(filepath):
        return f"Safety Error: Access denied to sensitive file: {filepath}"
    return None
