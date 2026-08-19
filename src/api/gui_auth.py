import os

from fastapi import Header, HTTPException, status


def require_gui_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("LLM_GRAPH_GUI_TOKEN", "")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GUI access token",
        )
