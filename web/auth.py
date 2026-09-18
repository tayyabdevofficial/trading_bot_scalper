import hashlib
import os
import secrets
import time
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from typing import Dict, Optional

# In-memory session store (Session ID -> User Email)
# Also backed by DB for persistent "remember me" sessions
ACTIVE_SESSIONS: Dict[str, str] = {}

def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with random 16-byte salt."""
    salt = os.urandom(16)
    db_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + db_hash.hex()

def verify_password(stored_password: str, provided_password: str) -> bool:
    """Verify standard PBKDF2 hashed password."""
    try:
        salt_hex, hash_hex = stored_password.split(":")
        salt = bytes.fromhex(salt_hex)
        db_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return db_hash.hex() == hash_hex
    except Exception:
        return False

def create_session(email: str, db=None, persistent: bool = False) -> str:
    """Generate session ID and save to memory (and optionally DB for persistence)."""
    session_id = secrets.token_hex(32)
    ACTIVE_SESSIONS[session_id] = email
    if persistent and db:
        # Store in DB with expiry timestamp (1 year)
        expiry = int(time.time()) + 31536000
        db.set_state(f"session_{session_id}", {"email": email, "expiry": expiry})
    return session_id

def destroy_session(session_id: str, db=None):
    """Delete session from memory and DB."""
    if session_id in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[session_id]
    if db:
        try:
            db.set_state(f"session_{session_id}", None)
        except Exception:
            pass

def restore_sessions_from_db(db):
    """Load persistent sessions from DB into memory on server startup."""
    try:
        now = int(time.time())
        # DB state keys are not easily iterable, so we rely on sessions
        # being re-validated lazily (see get_current_user_with_db)
    except Exception:
        pass

def get_current_user(request: Request) -> str:
    """Verify session from request cookies (memory only)."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return ACTIVE_SESSIONS[session_id]

def get_current_user_with_db(request: Request, db) -> Optional[str]:
    """Verify session from memory, falling back to DB for persistent sessions."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None

    # Check in-memory first
    if session_id in ACTIVE_SESSIONS:
        return ACTIVE_SESSIONS[session_id]

    # Fall back to DB (persistent/remember-me session)
    if db:
        try:
            stored = db.get_state(f"session_{session_id}")
            if stored and isinstance(stored, dict):
                expiry = stored.get("expiry", 0)
                email = stored.get("email")
                if email and int(time.time()) < expiry:
                    # Restore to memory
                    ACTIVE_SESSIONS[session_id] = email
                    return email
        except Exception:
            pass
    return None

async def check_admin_auth(request: Request, db=None):
    """Dependency / helper to enforce authentication, redirecting to login page on fail."""
    session_id = request.cookies.get("session_id")

    # Check in-memory first
    if session_id and session_id in ACTIVE_SESSIONS:
        return ACTIVE_SESSIONS[session_id]

    # Fall back to DB persistent session
    if session_id and db:
        try:
            stored = db.get_state(f"session_{session_id}")
            if stored and isinstance(stored, dict):
                import time as _time
                expiry = stored.get("expiry", 0)
                email = stored.get("email")
                if email and int(_time.time()) < expiry:
                    ACTIVE_SESSIONS[session_id] = email
                    return email
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session expired or invalid. Please login."
    )
