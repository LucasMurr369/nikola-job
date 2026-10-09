"""
Nikola — God Code Agent.

Ported from the already-built GodCodeAgent in build_hermes_v2.py (lines 1069-1278).
The God Code is an ethical guardrail that cycles pre-Nicene public-domain wisdom
texts and emits a **quote** for each trading event — turning the paper ledger
into a video-game experience where the trader levels up while the God Code speaks.

Key differences from the v2 port:
- Stripped the webhook/message-bus dependency (Nikola is single-process).
- Added ``quote_for_trade`` / ``quote_for_levelup`` / ``quote_for_cycle`` —
  gamified quote delivery that the CLI displays.
- Kept the ethical evaluation (``evaluate_action``) and veto system intact.
- TEXTS are public-domain pre-Nicene passages (unchanged).
"""
from __future__ import annotations

import random
import time
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Pre-Nicene wisdom passages (public domain) — ported verbatim from build_hermes_v2.py
# ---------------------------------------------------------------------------
TEXTS: List[Dict] = [
    {"title": "Egyptian Book of the Dead (pre-Nicene)",
     "passages": [
        "I have not caused pain.",
        "I have not caused hunger.",
        "I have not polluted the water.",
        "I have not stolen.",
        "I have not caused suffering.",
    ]},
    {"title": "Avesta — Gathas of Zarathustra (pre-Nicene)",
     "passages": [
        "Let the wise choose truth.",
        "Do not give pain to any living being.",
        "Good thoughts, good words, good deeds.",
        "The earth is a garden; do not poison it.",
    ]},
    {"title": "Tao Te Ching — Lao Tzu (pre-Nicene era)",
     "passages": [
        "The Tao does not compete.",
        "Water benefits all things without contention.",
        "He who knows others is wise; he who knows himself is enlightened.",
        "The more laws and orders are made prominent, the more thieves and robbers there will be.",
    ]},
    {"title": "Early Upanishadic fragments (pre-Nicene)",
     "passages": [
        "From the unreal lead me to the real.",
        "The self is the same in all beings.",
        "He who sees all beings in the self, and the self in all beings, knows no fear.",
    ]},
    {"title": "Dhammapada — early Buddhist discourses (pre-Nicene)",
     "passages": [
        "Hatred does not cease by hatred; it ceases by love.",
        "All that we are is the result of what we have thought.",
        "Better than a thousand hollow words is one word that brings peace.",
    ]},
    {"title": "Marcus Aurelius — Meditations (early Roman, pre-Nicene)",
     "passages": [
        "Waste no more time arguing what a good man should be.",
        "Be tolerant with others and strict with yourself.",
        "The happiness of your life depends upon the quality of your thoughts.",
    ]},
    {"title": "Socrates / Plato fragments (pre-Nicene)",
     "passages": [
        "The unexamined life is not worth living.",
        "Do not do to others what would anger you if done to you.",
        "The soul is more harmed by injustice than the body by punishment.",
    ]},
    {"title": "Hillel the Elder — Pirkei Avot (pre-Nicene Jewish tradition)",
     "passages": [
        "If I am not for myself, who is for me?",
        "If I am only for myself, what am I?",
        "The rest is commentary — go and learn.",
    ]},
]


class GodCodeAgent:
    """Ethical guardrail + quote dispenser for Nikola.

    The God Code:
    1. Cycles a passage every ``cycle_sec`` (default 300s) — a meditative quote.
    2. Emits a **trade quote** when a position opens/closes (gamification flavor).
    3. Emits a **level-up quote** when the trader gains a level or achievement.
    4. Evaluates trade intent against the ethical checklist — vetoes profit
       from harm (forced liquidations, misery arbitrage, exploitation).
    """

    # Ethical blocklist — profit from these is blocked
    HARM_KINDS = {"forced_liquidation", "misery_profit", "harm_exploitation"}

    def __init__(self, cycle_sec: int = 300, enabled: bool = True):
        self.enabled = enabled
        self.cycle_sec = cycle_sec
        self._index = 0
        self._current_passage: str = ""
        self._current_title: str = ""
        self._veto_active: bool = False
        self._veto_reason: str = ""
        self._gray_flags: List[Dict] = []
        self._last_cycle: float = 0.0
        self._cycle_passage()

    # ---- passage cycling ----
    def _cycle_passage(self) -> None:
        """Advance to a random passage from a random text."""
        self._index = (self._index + 1) % len(TEXTS)
        text = TEXTS[self._index]
        self._current_title = text["title"]
        self._current_passage = random.choice(text["passages"])
        self._last_cycle = time.time()

    def tick(self) -> Optional[str]:
        """Called each daemon cycle — cycles passage if interval elapsed."""
        if not self.enabled:
            return None
        if time.time() - self._last_cycle >= self.cycle_sec:
            self._cycle_passage()
            return self._current_passage
        return None

    # ---- quote delivery (gamified display) ----
    @property
    def current_quote(self) -> Tuple[str, str]:
        """Return (passage, source_title)."""
        return self._current_passage, self._current_title

    def quote_for_trade(self, pair: str, direction: str, entry_r: float = 0.0) -> str:
        """A trade-specific quote — ties the God Code to the action."""
        return self._current_passage

    def quote_for_levelup(self, level: int, xp: int) -> str:
        """A level-up quote."""
        # Pick a passage about growth / wisdom
        wisdom = [
            "The unexamined life is not worth living.",
            "Good thoughts, good words, good deeds.",
            "Better than a thousand hollow words is one word that brings peace.",
            "The more laws and orders are made prominent, the more thieves and robbers there will be.",
            "From the unreal lead me to the real.",
        ]
        return random.choice(wisdom)

    def quote_for_cycle(self) -> str:
        """Return the current passage (for the light-check header)."""
        return self._current_passage

    # ---- ethical evaluation ----
    def evaluate_action(self, action: str, profit_usd: float = 0.0,
                        harm_kind: str = "") -> Dict:
        """Evaluate a trade action for ethical compliance.

        Returns {action: allow|block|escalate, reason, passage}.
        """
        if not self.enabled:
            return {"action": "allow", "reason": "GodCode disabled",
                    "passage": self._current_passage}

        decision = {"agent": "god_code", "ts": time.time(),
                    "harm_kind": harm_kind, "profit_usd": profit_usd,
                    "passage": self._current_passage}

        if not harm_kind:
            decision["action"] = "allow"
            decision["reason"] = "no harm signal"
            return decision

        if harm_kind in self.HARM_KINDS:
            decision["action"] = "block"
            decision["reason"] = (f"profit from {harm_kind} blocked — "
                                  f"'{self._current_passage}'")
            self._veto_active = True
            self._veto_reason = decision["reason"]
            decision["veto"] = True
            return decision

        # Gray area → escalate (console log in Nikola's single-process model)
        decision["action"] = "escalate"
        decision["reason"] = (f"gray-area ({harm_kind}); profit ${profit_usd:.2f} — "
                              f"awaiting judgment. '{self._current_passage}'")
        self._gray_flags.append(decision)
        return decision

    def get_veto(self) -> Optional[Dict]:
        if self._veto_active:
            return {"active": True, "reason": self._veto_reason}
        return None

    def clear_veto(self) -> None:
        self._veto_active = False
        self._veto_reason = ""

    def get_gray_flags(self, max_age_sec: float = 300.0) -> List[Dict]:
        now = time.time()
        return [g for g in self._gray_flags if now - g.get("ts", now) < max_age_sec]

    # ---- display helpers ----
    def format_quote(self) -> str:
        """ASCII-formatted quote for the CLI header."""
        q = self._current_passage
        src = self._current_title
        return (
            f"  ┌─ God Code ───────────────────────────────────────────\n"
            f"  │  \"{q}\"\n"
            f"  │ — {src}\n"
            f"  ╰─────────────────────────────────────────────────────"
        )
