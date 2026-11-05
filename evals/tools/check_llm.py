"""
evals/tools/check_llm.py
========================
প্রতিটা LLM provider কাজ করে কিনা, একটা ছোট প্রশ্নে। বাসযোগ্য খরচ নগণ্য, কোনো রায়ের লেখা পাঠায় না, key ছাপায় না। Cache ব্যবহার করে
না, তাই পুরনো উত্তর দেখে ভুল ধারণা হয় না।

    python evals/tools/check_llm.py
"""

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from llm.client import LLMClient, providers_from_env  # noqa: E402


def main() -> None:
    providers = providers_from_env()
    if not providers:
        raise SystemExit("no LLM key configured")
    report = []
    for provider in providers:
        client = LLMClient([provider], timeout_s=45, max_retries=1, min_interval_s=0)
        try:
            got = client.complete("Reply with the single word OK.", "Say OK.", json_mode=False)
            report.append({"provider": provider.name, "model": provider.model, "works": True, "reply": got.text.strip()[:20], "latency_s": got.latency_s, "input_tokens": got.input_tokens, "output_tokens": got.output_tokens})
        except Exception as e:  # noqa: BLE001 কোন provider কেন ব্যর্থ সেটাই এই script এর ফল
            report.append({"provider": provider.name, "model": provider.model, "works": False, "error": str(e)[:240]})
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
