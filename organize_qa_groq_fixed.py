import os
import re
import json
import argparse
import textwrap
from pathlib import Path
from urllib import request, error

# config
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")  # paste key here
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
BATCH_SIZE    = 5


def check_api_key():
    if not GROQ_API_KEY:
        print("\nERROR: GROQ_API_KEY not set.")
        print("  1. Get a free key at: https://console.groq.com")
        print("  2. Run: export GROQ_API_KEY='gsk_...'")
        raise SystemExit(1)


def parse_qa_pairs(text: str) -> list[dict]:
    """
    Parse Q&A pairs from text. Supports:
      Q: / A:
      Question: / Answer:
      Human: / Assistant:
      Prompt: / Response:
      Blank-line separated alternating blocks (fallback)
    """
    pairs = []

    # BUG 4 수정: [::] → [:：] (한국어 콜론 포함)
    labeled = re.split(
        r'\n(?=(?:Q|Question|Human|Prompt)\s*[:：])',
        text, flags=re.IGNORECASE
    )

    for block in labeled:
        block = block.strip()
        if not block:
            continue
        qa_split = re.split(
            r'\n(?=(?:A|Answer|Assistant|Response)\s*[:：])',
            block, maxsplit=1, flags=re.IGNORECASE
        )
        if len(qa_split) == 2:
            # BUG 3 수정: r'^(?"Q...' → r'^(?:Q...'
            q = re.sub(r'^(?:Q|Question|Human|Prompt)\s*[:：]\s*', '', qa_split[0], flags=re.IGNORECASE).strip()
            a = re.sub(r'^(?:A|Answer|Assistant|Response)\s*[:：]\s*', '', qa_split[1], flags=re.IGNORECASE).strip()
            if q and a:
                pairs.append({"question": q, "answer": a})

    # BUG 1+2 수정: fallback과 return을 for 루프 밖으로 이동
    if not pairs:
        blocks = [b.strip() for b in re.split(r'\n{2,}', text) if b.strip()]
        for i in range(0, len(blocks) - 1, 2):
            pairs.append({"question": blocks[i], "answer": blocks[i + 1]})

    return pairs  # ← 루프 밖에 있어야 함


def classify_batch(pairs: list[dict], model: str) -> list[dict]:
    """Send a batch to Groq and get topic + summary per pair."""

    numbered = "\n\n".join(
        f"[{i+1}]\nQ: {p['question'][:300]}\nA: {p['answer'][:600]}"
        for i, p in enumerate(pairs)
    )

    prompt = textwrap.dedent(f"""
        You are a knowledge organizer. For each numbered Q&A pair below,
        return a JSON array. Each element must have:
          "index": the number in brackets (integer)
          "topic": short snake_case label e.g. "automation", "crypto_basics",
                   "systems_engineering", "python", "career_advice"
          "summary": one sentence, max 15 words, summarizing the exchange

        Rules:
        - Return ONLY the raw JSON array. No explanation, no markdown, no code fences.
        - Reuse the same topic label when the subject is the same across pairs.

        Q&A pairs:
        {numbered}

        JSON array:
    """).strip()

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1024,
    }).encode("utf-8")

    req = request.Request(
        GROQ_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        },
        method="POST"
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        body = e.read().decode("utf-8")
        # BUG 5 수정: "f\n ⚠..." → f"\n ⚠..." (f-string 위치 오류)
        print(f"\n   ⚠ HTTP {e.code}: {body[:300]}")
        if e.code == 429:
            print("   Rate limit hit — Groq free tier: ~30 req/min. Wait 10s and retry.")
        return []
    except Exception as e:
        print(f"\n   ⚠ Request error: {e}")
        return []

    raw = data["choices"][0]["message"]["content"].strip()

    # Strip accidental markdown fences
    raw = re.sub(r'^```[a-z]*\n?', '', raw)
    raw = re.sub(r'\n?```$', '', raw)
    # BUG 6 수정: r'[.*\]' → r'\[.*\]' (정규식 이스케이프 오류)
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        raw = match.group(0)

    try:
        classifications = json.loads(raw)
    except json.JSONDecodeError:
        print(f"\n   ⚠ JSON parse error — skipping batch")
        print(f"   Raw preview: {raw[:200]}")
        return []

    enriched = []
    for item in classifications:
        idx = item.get("index", 0) - 1
        if 0 <= idx < len(pairs):
            enriched.append({
                **pairs[idx],
                "topic":   item.get("topic", "uncategorized").strip().lower().replace(" ", "_"),
                "summary": item.get("summary", "")
            })
    return enriched


def write_topic_files(classified: list[dict], output_dir: Path) -> dict:
    """Write one .md file per topic."""
    output_dir.mkdir(parents=True, exist_ok=True)

    topics: dict[str, list[dict]] = {}
    for item in classified:
        t = item["topic"]
        # BUG 7 수정: setdefauly → setdefault
        topics.setdefault(t, []).append(item)

    # BUG 8 수정: for loop 변수명 item → items (classified 루프의 item과 충돌)
    for topic, items in sorted(topics.items()):
        filepath = output_dir / f"{topic}.md"
        lines = [
            f"# {topic.replace('_', ' ').title()}\n",
            f"_{len(items)} Q&A pair{'s' if len(items) > 1 else ''}_\n\n---\n"
        ]
        for i, item in enumerate(items, 1):
            lines.append(f"## {i}. {item['summary']}\n")
            lines.append(f"**Q:** {item['question']}\n")
            # BUG 9 수정: "n**A:**" → "\n**A:**"
            lines.append(f"\n**A:** {item['answer']}\n")
            lines.append("\n---\n")
        filepath.write_text("\n".join(lines), encoding="utf-8")
        print(f"  ✓  {filepath}  ({len(items)} pairs)")

    return topics


def main():
    parser = argparse.ArgumentParser(
        description="Organize Q&A .txt files by topic — free, using Groq API"
    )
    parser.add_argument("--input",  "-i", required=True,          help="Input .txt file")
    parser.add_argument("--output", "-o", default="./topics",     help="Output folder (default: ./topics)")
    parser.add_argument("--model",  "-m", default=DEFAULT_MODEL,  help=f"Groq model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    check_api_key()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        raise SystemExit(1)

    print(f"\n  Model : {args.model}")
    print(f"  Input : {input_path}")
    print(f"  Output: {args.output}/\n")

    text = input_path.read_text(encoding="utf-8")

    print("Parsing Q&A pairs...")
    pairs = parse_qa_pairs(text)
    if not pairs:
        print("ERROR: No Q&A pairs detected. Supported formats:")
        print("  Q: ... / A: ...")
        print("  Human: ... / Assistant: ...")
        print("  Question: ... / Answer: ...")
        print("  Blank-line separated alternating blocks")
        raise SystemExit(1)
    print(f"  Found {len(pairs)} pairs\n")

    classified = []
    total_batches = (len(pairs) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Classifying via Groq (batch size: {BATCH_SIZE})...")

    # BUG 10 수정: lent(pairs) → len(pairs)
    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  Batch {batch_num}/{total_batches}  ({len(batch)} pairs)...", end=" ", flush=True)
        # BUG 11 수정: classfiy_batch → classify_batch
        result = classify_batch(batch, args.model)
        # BUG 12 수정: classified.extenc → classified.extend
        classified.extend(result)
        print(f"done  ({len(result)} classified)")

    # BUG 13 수정: if not classified 이하 블록을 for 루프 밖으로 이동
    if not classified:
        print("\nERROR: Nothing was classified. Check your API key and model name.")
        raise SystemExit(1)

    print(f"\nWriting topic files...")
    topics = write_topic_files(classified, Path(args.output))

    print(f"\nDone — {len(classified)} pairs → {len(topics)} topic files:")
    for t, items in sorted(topics.items()):
        print(f"  • {t}.md  ({len(items)} pairs)")
    print()


if __name__ == "__main__":
    main()