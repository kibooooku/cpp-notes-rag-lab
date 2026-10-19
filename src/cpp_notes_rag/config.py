"""Non-secret reproducible settings; the key is read only at request time."""

import hashlib
import json
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    root: Path = Path(__file__).resolve().parents[2]
    source_dir: Path | None = None
    storage_dir: Path | None = None
    model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_revision: str = "main"
    top_k: int = 3
    chunk_max_tokens: int = 480
    generation_model: str = "deepseek-flash"
    api_base_url: str = "https://api.deepseek.com"
    api_timeout_seconds: float = 90.0
    max_output_tokens: int = 1400
    temperature: float = 0.0
    bm25_min_score: float = 0.0
    vector_min_score: float = 0.0
    budget_cny: float = 20.0
    input_cny_per_million: float = 2.0
    cached_input_cny_per_million: float = 0.04
    output_cny_per_million: float = 8.0
    historical_allowance_cny: float = 0.01

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.source_dir = Path(self.source_dir or self.root / "问答素材").resolve()
        self.storage_dir = Path(self.storage_dir or self.root / "storage").resolve()
        if self.top_k != 3:
            raise ValueError("本实验固定 Top-3。修改实验协议后才能改变 top_k。")
        if self.budget_cny <= 0 or self.budget_cny > 20:
            raise ValueError("项目总预算必须在 0–20 元范围内。")

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        root = Path(root or os.environ.get("CPP_RAG_ROOT", Path(__file__).resolve().parents[2])).resolve()
        path = root / "config" / "default.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        allowed = {f.name for f in fields(cls)} - {"root"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"未知配置项：{sorted(unknown)}")
        for name in ("source_dir", "storage_dir"):
            if name in data:
                data[name] = root / data[name]
        if os.environ.get("DEEPSEEK_MODEL"):
            data["generation_model"] = os.environ["DEEPSEEK_MODEL"]
        return cls(root=root, **data)

    def to_dict(self) -> dict:
        return {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self).items()}

    @property
    def config_hash(self) -> str:
        # Machine paths do not change the experiment configuration fingerprint.
        data = self.to_dict()
        for key in ("root", "source_dir", "storage_dir"):
            data.pop(key)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def api_key(self) -> str:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        path = self.root / "secrets" / "deepseek_api_key.txt"
        if not key and path.exists():
            key = path.read_text(encoding="utf-8-sig").strip()
        if not key or "\n" in key or "\r" in key:
            raise ValueError("请设置 DEEPSEEK_API_KEY，或将单行密钥放在 secrets/deepseek_api_key.txt。")
        return key
