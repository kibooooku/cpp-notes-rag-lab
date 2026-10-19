"""Persistent, locked budget reservations, including requests with unknown usage."""

import json
import math
import os
import uuid
from datetime import UTC, datetime

from filelock import FileLock


class BudgetError(RuntimeError):
    pass


class CostTracker:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.storage_dir / "cost_ledger.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock", timeout=10)

    @staticmethod
    def _money(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    @staticmethod
    def _usage_counts(usage):
        if not isinstance(usage, dict):
            return None
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if (not isinstance(prompt, int) or isinstance(prompt, bool) or prompt < 0
                or not isinstance(completion, int) or isinstance(completion, bool) or completion < 0):
            return None
        cached = usage.get("prompt_cache_hit_tokens", 0)
        if not isinstance(cached, int) or isinstance(cached, bool) or not 0 <= cached <= prompt:
            return None
        return prompt, completion, cached

    def _validate(self, data):
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("invalid ledger version")
        historical = data.get("historical_allowance_cny")
        if not self._money(historical) or historical < self.settings.historical_allowance_cny:
            raise ValueError("invalid historical allowance")
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise TypeError("invalid ledger entries")
        identifiers = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError("invalid ledger entry")
            request_id = entry.get("id")
            if not isinstance(request_id, str) or not request_id.strip() or request_id in identifiers:
                raise ValueError("invalid or duplicate request id")
            identifiers.add(request_id)
            if entry.get("state") not in {"reserved", "settled"}:
                raise ValueError("invalid request state")
            if not self._money(entry.get("reserved_cny")) or not self._money(entry.get("held_cny")):
                raise ValueError("invalid request amount")
            if entry["state"] == "reserved":
                if entry["held_cny"] < entry["reserved_cny"] or entry.get("usage") is not None:
                    raise ValueError("unknown request usage cannot release held funds")
            elif self._usage_counts(entry.get("usage")) is None:
                raise ValueError("settled request requires valid usage")
        if not math.isfinite(self._total(data)):
            raise ValueError("invalid total amount")
        return data

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "historical_allowance_cny": self.settings.historical_allowance_cny,
                    "entries": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return self._validate(data)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise BudgetError("费用账本无法验证；为避免重置预算，已停止 API 调用。请保留账本核查。") from exc

    def _save(self, data):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    @staticmethod
    def _total(data):
        return data["historical_allowance_cny"] + sum(e["held_cny"] for e in data["entries"])

    def snapshot(self):
        with self.lock:
            data = self._load()
            return {"limit_cny": self.settings.budget_cny, "upper_estimate_cny": self._total(data),
                    "requests": len(data["entries"]),
                    "unsettled_requests": sum(e["state"] != "settled" for e in data["entries"]),
                    "historical_allowance_cny": data["historical_allowance_cny"],
                    "note": "按官方高峰价的费用上界；含历史预留与用量未知请求，不代表实际扣款。"}

    def reserve(self, messages, max_output_tokens):
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise BudgetError("最大输出 token 数必须是正整数，未创建费用预留。")
        # UTF-8 byte length bounds common byte/token encodings; add protocol overhead.
        prompt_bound = sum(len(m["content"].encode("utf-8")) + 128 for m in messages) + 256
        amount = (prompt_bound * self.settings.input_cny_per_million
                  + max_output_tokens * self.settings.output_cny_per_million) / 1_000_000
        if not self._money(amount):
            raise BudgetError("费用预留无法验证，已停止 API 调用。")
        with self.lock:
            data = self._load()
            if self._total(data) + amount > self.settings.budget_cny:
                raise BudgetError("本次最大费用预留将超过项目累计 20 元预算，已停止调用。")
            request_id = uuid.uuid4().hex
            data["entries"].append({"id": request_id, "time": datetime.now(UTC).isoformat(),
                                    "model": self.settings.generation_model, "state": "reserved",
                                    "held_cny": amount, "reserved_cny": amount, "usage": None})
            self._save(data)
            return request_id

    def settle(self, request_id, usage):
        # Missing/invalid usage stays fully reserved; a timeout must never become free.
        counts = self._usage_counts(usage)
        if counts is None:
            return
        prompt, completion, cached = counts
        amount = ((prompt - cached) * self.settings.input_cny_per_million
                  + cached * self.settings.cached_input_cny_per_million
                  + completion * self.settings.output_cny_per_million) / 1_000_000
        if not self._money(amount):
            raise BudgetError("API 用量费用无法验证，保留原费用预留。")
        with self.lock:
            data = self._load()
            entry = next((e for e in data["entries"] if e["id"] == request_id), None)
            if entry is None:
                raise BudgetError("费用账本没有本次请求的预留，未改动记录。")
            if entry["state"] == "settled":
                if self._usage_counts(entry["usage"]) != counts:
                    raise BudgetError("同一请求已经结算且用量不一致，未改动记录。")
                return
            entry.update(state="settled", held_cny=amount, usage=usage)
            self._save(data)
            if self._total(data) > self.settings.budget_cny:
                raise BudgetError("服务端用量超过预留界限；已停止后续请求，请核对费用。")
