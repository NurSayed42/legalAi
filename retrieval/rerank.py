"""
retrieval/rerank.py
===================
Cross-encoder reranker, api_v3 এর মতো একই model, max_length, text কাটা আর CPU int8 quantization।

Score disk এ cache হয়, তাই থেমে গেলে আবার চালালে আগের জোড়া আর হিসাব করে না। Threshold এখানে নেই: কোনটা
বাদ যাবে সেটা caller এর সিদ্ধান্ত, আর threshold এর প্রভাব মাপা হয় score দেখে।
"""

import hashlib
import json
import warnings
from pathlib import Path


def pair_key(model: str, max_length: int, query: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{max_length}\0{query}\0{text}".encode()).hexdigest()


class CrossEncoderReranker:
    def __init__(self, model: str, max_length: int, text_chars: int, cache_path: Path, batch_size: int = 8):
        self.model_name = model
        self.max_length = max_length
        self.text_chars = text_chars
        self.batch_size = batch_size
        self.cache_path = cache_path
        self.cache: dict[str, float] = {}
        if cache_path.exists():
            with cache_path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        row = json.loads(line)
                        self.cache[row["k"]] = row["s"]
        self._encoder = None

    def _load(self):
        if self._encoder is None:
            import torch
            from sentence_transformers import CrossEncoder

            self._encoder = CrossEncoder(self.model_name, max_length=self.max_length)
            if not torch.cuda.is_available():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    torch.ao.quantization.quantize_dynamic(self._encoder.model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
        return self._encoder

    def score(self, query: str, texts: list[str]) -> list[float]:
        clipped = [t[: self.text_chars] for t in texts]
        keys = [pair_key(self.model_name, self.max_length, query, t) for t in clipped]
        todo = [(k, t) for k, t in dict(zip(keys, clipped)).items() if k not in self.cache]
        if todo:
            scores = self._load().predict([(query, t) for _, t in todo], batch_size=self.batch_size, show_progress_bar=False)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8", newline="\n") as fh:
                for (k, _), s in zip(todo, scores):
                    self.cache[k] = float(s)
                    fh.write(json.dumps({"k": k, "s": float(s)}) + "\n")
        return [self.cache[k] for k in keys]
