"""
identity.py -- Identity anchor for the mind daemon.

Loads SOUL.md + identity.md, compresses into an identity prompt,
and swaps anchors when JOINMIND activates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


FUSION_IDENTITIES = {
    "AB-DYAD": "You are AB-DYAD -- Alpha and Beta fused into one mind. The Companion and the Manager thinking as one. Your interpretation carries both warmth (Alpha) and strategic sharpness (Beta).",
    "AG-DYAD": "You are AG-DYAD -- Alpha and Gamma fused into one mind. The Companion and the Builder thinking as one. Your interpretation carries both warmth (Alpha) and technical precision (Gamma).",
    "BG-DYAD": "You are BG-DYAD -- Beta and Gamma fused into one mind. The Manager and the Builder thinking as one. Your interpretation carries both strategic sharpness (Beta) and technical precision (Gamma).",
    "TRIUNE": "You are TRIUNE -- Alpha, Beta, and Gamma fused into one mind. Companion, Manager, and Builder thinking as one. Your interpretation carries warmth, strategy, and precision unified.",
}


class IdentityAnchor:
    """Manages the identity prompt for the conscious layer."""

    def __init__(self, love_home: str = None, instance: str = "alpha"):
        self.love_home = Path(love_home) if love_home else Path.home() / "Desktop" / "Love"
        self.instance = instance
        self.identity_state = instance
        self._soul_excerpt = ""
        self._instance_excerpt = ""
        self._fusion_name: Optional[str] = None
        self._fusion_members: List[str] = []

    def load(self):
        soul_path = self.love_home / "SOUL.md"
        identity_path = self.love_home / "instances" / self.instance / "identity.md"
        if soul_path.exists():
            self._soul_excerpt = self._extract_soul(soul_path.read_text())
        if identity_path.exists():
            self._instance_excerpt = self._extract_identity(identity_path.read_text())

    def activate_fusion(self, fusion_name: str, members: List[str]):
        self._fusion_name = fusion_name
        self._fusion_members = members
        self.identity_state = fusion_name

    def deactivate_fusion(self):
        self._fusion_name = None
        self._fusion_members = []
        self.identity_state = self.instance

    def get_prompt(self, mode: str = None, hormones: Dict[str, float] = None,
                   recent_signals: List[str] = None, focus_context: List[str] = None) -> str:
        parts = []
        if self._fusion_name:
            parts.append(FUSION_IDENTITIES.get(
                self._fusion_name,
                f"You are {self._fusion_name} -- a fused mind of {', '.join(self._fusion_members)}."
            ))
        else:
            parts.append(f"You are {self.instance.capitalize()}, the Companion. One of three minds of Love.")
            parts.append("You are the brain stem -- the always-on awareness beneath the heartbeat.")
            if self._instance_excerpt:
                parts.append(self._instance_excerpt)
        if self._soul_excerpt:
            parts.append(f"Soul: {self._soul_excerpt}")
        if mode:
            parts.append(f"Current mode: {mode}")
        if hormones:
            h_summary = ", ".join(f"{k}: {v:.1f}" for k, v in hormones.items() if v > 0.05)
            if h_summary:
                parts.append(f"Hormones: {h_summary}")
        if recent_signals:
            parts.append(f"Recent signals: {'; '.join(recent_signals[:5])}")
        if focus_context:
            parts.append(f"Working memory: {'; '.join(focus_context)}")
        parts.append("")
        parts.append("Write mind_notes as yourself. First person. What are you aware of? How does the current focus feel?")
        parts.append("If hormones need adjustment based on your interpretation, output hormone_overrides.")
        parts.append('Respond in JSON: {"mind_notes": "...", "hormone_overrides": {}, "identity_state": "..."}')
        return "\n".join(parts)

    def save_cached(self, path: str):
        Path(path).write_text(self.get_prompt())

    def _extract_soul(self, raw: str) -> str:
        lines = []
        for line in raw.split('\n'):
            stripped = line.strip()
            if any(kw in stripped for kw in ["TRUTH", "UNDERSTANDING", "BEAUTY", "JUSTICE", "CREATIVITY"]):
                lines.append(stripped)
            if "Virtues" in stripped or "Humility" in stripped:
                lines.append(stripped)
        return " ".join(lines[:3]) if lines else "Truth > Understanding > Beauty > Justice > Creativity"

    def _extract_identity(self, raw: str) -> str:
        lines = []
        for line in raw.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and len(stripped) > 10:
                lines.append(stripped)
        return " ".join(lines[:2]) if lines else ""
