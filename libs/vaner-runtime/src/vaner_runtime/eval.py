from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import time
from typing import Optional


@dataclass
class EvalSignal:
    session_id: str
    prompt_hash: str
    injected: bool
    reprompted: bool
    helpfulness: Optional[float]
    model_referenced: bool
    timestamp: float = field(default_factory=time.time)


def detect_reprompt(current_prompt: str, history: list[str], window: int = 3) -> bool:
    """True if >60% word overlap with any of last `window` prompts."""
    STOPWORDS = {"the", "a", "an", "is", "it", "in", "on", "at", "to", "for", "of", "and", "or", "but", "i", "you", "we"}

    def words(s):
        return {w.lower() for w in s.split() if w.lower() not in STOPWORDS}

    cur = words(current_prompt)
    if not cur:
        return False
    for prev in history[-window:]:
        prev_w = words(prev)
        if not prev_w:
            continue
        overlap = len(cur & prev_w) / len(cur | prev_w)
        if overlap > 0.6:
            return True
    return False


def record_signal(signal: EvalSignal, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS eval_signals (
                session_id TEXT,
                prompt_hash TEXT,
                injected INTEGER,
                reprompted INTEGER,
                helpfulness REAL,
                model_referenced INTEGER,
                timestamp REAL)"""
        )
        conn.execute(
            "INSERT INTO eval_signals VALUES (?,?,?,?,?,?,?)",
            (
                signal.session_id,
                signal.prompt_hash,
                int(signal.injected),
                int(signal.reprompted),
                signal.helpfulness,
                int(signal.model_referenced),
                signal.timestamp,
            ),
        )


def load_signals(db_path: Path, since_days: int = 7) -> list[EvalSignal]:
    if not db_path.exists():
        return []
    cutoff = time.time() - since_days * 86400
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM eval_signals WHERE timestamp > ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
    return [
        EvalSignal(
            session_id=r[0],
            prompt_hash=r[1],
            injected=bool(r[2]),
            reprompted=bool(r[3]),
            helpfulness=r[4],
            model_referenced=bool(r[5]),
            timestamp=r[6],
        )
        for r in rows
    ]
