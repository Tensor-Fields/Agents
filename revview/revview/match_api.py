"""Matcher that calls the Claude API (needs `pip install anthropic` and credentials:
ANTHROPIC_API_KEY, or an `ant auth login` profile).

Both manuscripts go into a cached system prompt; comments are sent in batches, so every batch
after the first reads the documents from the prompt cache."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from . import tasks

DEFAULT_MODEL = "claude-opus-5-5"
# $/MTok: input, output, cache write (5 min), cache read
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
          "claude-fable-5-1": (10.0, 50.0, 12.5, 0.25), "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01)}


def _client():
    try:
        import anthropic
    except ImportError:
        raise SystemExit("The api matcher needs the Anthropic SDK: pip install anthropic")
    return anthropic, anthropic.Anthropic()


def match(old, new, comments, hints=None, model=DEFAULT_MODEL, effort="high", batch_size=10, jobs=4,
          log=sys.stderr):
    anthropic, client = _client()
    system = [
        {"type": "text", "text": tasks.INSTRUCTIONS},
        {"type": "text", "text": tasks.passage_listing(old, "commented") + "\n\n" + tasks.passage_listing(new, "revised"),
         "cache_control": {"type": "ephemeral"}},
    ]
    records = tasks.task_records(comments, hints)
    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}

    def run(batch):
        params = dict(
            model=model, max_tokens=64000, system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": tasks.RESULT_SCHEMA}},
            messages=[{"role": "user", "content": tasks.batch_prompt(batch)}],
        )
        if model in ("claude-opus-5-5", "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5-5"):
            # on a safety-classifier refusal, let the API retry on its recommended fallback model
            params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            with client.beta.messages.stream(**params) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise SystemExit("Claude API: no valid credentials (set ANTHROPIC_API_KEY or run `ant auth login`).")
        except anthropic.BadRequestError as e:
            raise SystemExit(f"Claude API rejected the request: {e.message}")
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            print(f"  batch {batch[0]['id']}-{batch[-1]['id']} failed after retries: {e}", file=log)
            return []
        u = msg.usage
        usage["input"] += u.input_tokens or 0
        usage["output"] += u.output_tokens or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        if msg.stop_reason == "refusal":
            print(f"  batch {batch[0]['id']}-{batch[-1]['id']}: request declined by the model", file=log)
            return []
        if msg.stop_reason == "max_tokens":
            print(f"  batch {batch[0]['id']}-{batch[-1]['id']}: output truncated; use a smaller --batch-size", file=log)
            return []
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            rows = json.loads(text)["matches"]
        except (json.JSONDecodeError, KeyError):
            print(f"  batch {batch[0]['id']}-{batch[-1]['id']}: unparseable response", file=log)
            return []
        print(f"  matched comments {batch[0]['id']}-{batch[-1]['id']}", file=log)
        return rows

    rows = []
    if batches:
        rows += run(batches[0])  # first call writes the prompt cache; the rest read it in parallel
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            for r in ex.map(run, batches[1:]):
                rows += r
    p = PRICES.get(model)
    cost = (f" ~${(usage['input'] * p[0] + usage['output'] * p[1] + usage['cache_write'] * p[2] + usage['cache_read'] * p[3]) / 1e6:.2f}"
            if p else "")
    print(f"  tokens: in {usage['input']}, out {usage['output']}, cache write {usage['cache_write']}, "
          f"cache read {usage['cache_read']}{cost}", file=log)
    got = {m["id"]: m for m in tasks.to_matches(rows, old, new)}
    return [got.get(c["id"]) or {"id": c["id"], "old": None, "new": None, "status": "unclear",
                                  "confidence": "low", "note": "no API result"} for c in comments]
