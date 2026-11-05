"""
evals/fake_llm.py
=================
Network ছাড়া pipeline এর পুরো পথ চালানোর জন্য নকল LLM। সব সময় একই উত্তর দেয় আর sources এর প্রথম আইন আর মামলা উদ্ধৃত করে।
এর ফল শুধু প্রমাণ করে পরিবেশ চলে; উত্তরের মান সম্পর্কে কিছুই বলে না, তাই report এ baseline হিসেবে চলে না।
"""

import json
import re

_LAW = re.compile(r"^### Act: (.+)\nSection: (.+)$", re.MULTILINE)
_CASE = re.compile(r"^### Case: (.+)$", re.MULTILINE)


def fake_call_llm(system_prompt: str, user_content: str, json_mode: bool = True, schema=None) -> str:
    if "query router" in system_prompt:
        question = user_content.rsplit("বর্তমান প্রশ্ন:", 1)[-1].strip()
        return json.dumps({"rewritten_query": question, "sub_queries": [question], "intent": "general", "follow_up_options": []}, ensure_ascii=False)
    laws = _LAW.findall(user_content)
    cases = _CASE.findall(user_content)
    return json.dumps({
        "short_answer": "Fake answer for the environment test.",
        "applicable_laws": [{"act": a, "section": s, "what_it_says": "fake", "how_it_applies": "fake"} for a, s in laws[:1]],
        "relevant_cases": [{"case_no": c, "year": None, "division": "HCD", "holding": "fake"} for c in cases[:1]],
        "procedure": {"steps": ["1. fake"], "court": "fake", "deadline": "fake", "documents": ["fake"], "cost": "fake", "penalties": "fake"},
        "risks": [],
        "when_to_see_lawyer": "fake",
        "follow_up_options": [],
    }, ensure_ascii=False)
