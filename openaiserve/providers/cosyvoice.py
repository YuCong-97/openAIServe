from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class CosyVoice3Provider:
    """Reserved provider boundary for future CosyVoice 3 audio generation."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.enabled = bool(config.get("enabled", False))

    async def speech(self, request: dict[str, Any]) -> bytes:
        if not self.enabled:
            raise HTTPException(
                status_code=501,
                detail=(
                    "CosyVoice 3 audio generation is reserved but not enabled. "
                    "Set providers.cosyvoice3.enabled=true after adding a CosyVoice 3 adapter."
                ),
            )
        raise HTTPException(
            status_code=501,
            detail="CosyVoice 3 adapter is not implemented yet; the API boundary is ready for the future provider.",
        )

