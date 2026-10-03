"""Optional LLM classifier for borderline vacancies only. Disabled by default."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import requests

from src.utils.logging import get_logger

log = get_logger("llm")
API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """You classify bank job vacancies for a senior Finance/Risk leadership profile.
Answer ONLY with JSON: {{"leadership_role": bool, "relevant_function": bool, "germany": bool, "confidence": 0-1, "reason": "..."}}
leadership_role = the role manages people/a function (Head, Director, team lead, Abteilungsleiter, ...), not just a senior individual contributor.
relevant_function = Finance, Controlling, Risk, Capital/ICAAP, Regulatory Reporting, Treasury/Liquidity, Finance data/transformation.
germany = the job is located in Germany.

Title: {title}
Bank: {bank}
Location: {location}
Department: {department}
Description (truncated): {description}
"""


@dataclass
class LlmVerdict:
    leadership_role: bool
    relevant_function: bool
    germany: bool
    confidence: float
    reason: str

    def accepts(self, min_confidence: float) -> bool:
        return (self.leadership_role and self.relevant_function and self.germany
                and self.confidence >= min_confidence)


class LlmClassifier:
    def __init__(self, api_key: str, model: str, min_confidence: float = 0.7,
                 max_calls: int = 50, timeout: float = 30, session=None):
        self.api_key, self.model = api_key, model
        self.min_confidence, self.max_calls, self.timeout = min_confidence, max_calls, timeout
        self.calls = 0
        self.session = session or requests.Session()

    def available(self) -> bool:
        return bool(self.api_key) and self.calls < self.max_calls

    def classify(self, job) -> Optional[LlmVerdict]:
        if not self.available():
            return None
        self.calls += 1
        prompt = PROMPT.format(title=job.title, bank=job.bank_name, location=job.location,
                               department=job.department, description=job.description[:2500])
        try:
            resp = self.session.post(
                API_URL, timeout=self.timeout,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            return self.parse(resp.json()["content"][0]["text"])
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            log.warning("LLM classification failed for '%s': %s", job.title, exc)
            return None

    @staticmethod
    def parse(text: str) -> LlmVerdict:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON in LLM answer")
        d = json.loads(m.group(0))
        return LlmVerdict(bool(d["leadership_role"]), bool(d["relevant_function"]), bool(d["germany"]),
                          float(d.get("confidence", 0)), str(d.get("reason", "")))
