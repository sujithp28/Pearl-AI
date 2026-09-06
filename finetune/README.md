# Fine-tuning Pearl's planning model

Teaches the local model to emit valid plan JSON reliably — the single
biggest weakness of the bundled 1.5B.

## Why this, and not something else

`src/prompts/planning.txt` already states, in plain English, that
`"hello"` must return tool `"none"` and that single words which are not
file names must too. The 1.5B ignores both:

| Input | What it did | What the prompt says |
|---|---|---|
| `hello` | `read_file("hello.py")` | `tool: "none"` |
| `lpoe` | created `lpoe.py` | `tool: "none"` |

The model does not lack coding knowledge. It fails to follow a
constrained output format, which is the first thing a small model loses
and the cheapest thing LoRA restores.

Everything else you might fine-tune for — better code, better
explanations — is far better solved by [switching models or using a
remote one](../README.md#model-routing).

## Hardware: read this before starting

**A 4 GB GPU cannot run this.** Not a tuning problem — Pearl's planning
prompt is ~6.5k tokens, and the activations for it dominate memory even
with 4-bit weights. `train_lora.py` checks and refuses up front rather
than failing with an OOM after a 3 GB download.

| Setup | Verdict |
|---|---|
| GTX 1650 (4 GB) | ✗ Not enough |
| **Free Colab T4 (16 GB)** | ✓ **Recommended** |
| Any ≥12 GB GPU | ✓ |
| CPU only | ✗ Weeks per epoch |

Training the **0.5B** instead does fit smaller cards:
`--base-model Qwen/Qwen2.5-0.5B-Instruct`.

## Steps

### 1. Build the dataset (locally)

```bash
python -m finetune.build_dataset --out finetune/data/plans.jsonl
```

Generates `(prompt, correct_plan)` pairs using Pearl's **real** planning
prompt — the same template the planner renders at inference. Training on
a paraphrase teaches a mapping the model is never shown.

Pairs are correct by construction: a greeting maps to `none`, `read X.py`
maps to `read_file`, and the tool registry supplies valid names and
argument shapes. No human labelling.

> The generator produces ~150 examples. That is a **starting point, not a
> finished dataset** — enough to verify the pipeline, not enough to
> generalise. Expect real gains from a few thousand, and add pairs drawn
> from your own repositories.

### 2. Train (on a GPU)

Upload `finetune/` and `plans.jsonl` to Colab, then:

```bash
pip install -q transformers peft datasets bitsandbytes accelerate
python -m finetune.train_lora --data finetune/data/plans.jsonl
```

~15–30 minutes on a T4 for a small dataset. Produces a LoRA adapter of a
few MB, not a full model copy.

### 3. Merge and convert to GGUF

The only step outside this repo, because it needs `llama.cpp`'s converter:

```bash
# Merge the adapter into the base weights
python - <<'PY'
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-1.5B-Instruct")
merged = PeftModel.from_pretrained(base, "finetune/adapter").merge_and_unload()
merged.save_pretrained("finetune/merged")
AutoTokenizer.from_pretrained("finetune/adapter").save_pretrained("finetune/merged")
PY

# Convert to GGUF and quantise
git clone https://github.com/ggerganov/llama.cpp
python llama.cpp/convert_hf_to_gguf.py finetune/merged --outfile pearl-planner.gguf
./llama.cpp/llama-quantize pearl-planner.gguf pearl-planner-q4_k_m.gguf Q4_K_M
```

### 4. Point Pearl at it

Copy the `.gguf` into `~/.pearl/models/`, then in `.env`:

```bash
LOCAL_MODEL_FILE=pearl-planner-q4_k_m.gguf
```

Pearl already supports a custom GGUF — no code changes. Restart:

```bash
python -m src.api
```

## Judging whether it worked

Run the cases that failed before:

```bash
python -m src.cli --json "hello"    # expect: one "none" step, no files
python -m src.cli --json "lpoe"     # expect: clarification, no files
python -m src.cli --json "read README.md"   # expect: read_file
```

The third matters most. A fine-tune that fixes the first two by making
the model answer `none` to *everything* has not helped — it has traded
one failure for a worse one. Check that real requests still plan.

## What this will not fix

- **Reasoning quality.** LoRA on 150 examples teaches output format, not
  understanding. Multi-step planning stays weak.
- **Speed.** Same size, same latency. For that, use the GPU you already
  have (CUDA build of `llama-cpp-python`) or a remote model.
- **Non-English fluency.** Bounded by the base model.

If the goal is "Pearl plans well", a remote model in the `hybrid`
profile does more, immediately, with no training. This path is worth it
when you want a *local* model that follows Pearl's format — privacy,
offline use, or no per-token cost.
