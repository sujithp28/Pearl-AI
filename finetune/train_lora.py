"""
QLoRA fine-tune of Pearl's planning model.

Run this on a GPU with at least ~12 GB of VRAM — a free Colab T4 is
enough. It will not run on a 4 GB card: 4-bit weights for a 1.5B fit,
but the activations and optimiser state for a 6.5k-token planning
prompt do not. That is a hard memory limit, not a tuning knob, and
attempting it locally fails with an OOM after the download.

Why LoRA rather than full fine-tuning
-------------------------------------
Full fine-tuning a 1.5B needs roughly 24 GB and rewrites every weight,
which risks losing the general coding ability Pearl relies on
elsewhere. LoRA trains a small number of adapter weights, needs an
order of magnitude less memory, and is reversible — a bad run is
discarded by deleting a file rather than re-downloading the model.

Pipeline
--------
    1. python -m finetune.build_dataset          (locally, this repo)
    2. python -m finetune.train_lora             (on a GPU)
    3. merge + convert to GGUF                   (see README)
    4. point LOCAL_MODEL_FILE at the result      (one setting)

Only step 3 leaves this directory; Pearl already loads a custom GGUF.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Minimum VRAM for a 1.5B QLoRA run with Pearl-sized prompts. Measured
# on a T4; below this the run OOMs partway through the first epoch,
# which is worse than refusing up front.
MIN_VRAM_GB = 12

DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


def _check_environment() -> None:
    """
    Fail early and specifically rather than OOM after a 3 GB download.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "PyTorch is required. On Colab it is preinstalled; locally:\n"
            "  pip install torch --index-url https://download.pytorch.org/whl/cu121"
        ) from exc

    if not torch.cuda.is_available():
        raise SystemExit(
            "No CUDA GPU visible. This script needs one — CPU training a\n"
            "1.5B on 6.5k-token prompts would take weeks.\n\n"
            "Use a free Colab T4: Runtime > Change runtime type > T4 GPU."
        )

    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    name = torch.cuda.get_device_name(0)
    print(f"GPU: {name} ({vram_gb:.1f} GB)")

    if vram_gb < MIN_VRAM_GB:
        raise SystemExit(
            f"\n{name} has {vram_gb:.1f} GB; this run needs about "
            f"{MIN_VRAM_GB} GB.\n\n"
            "Pearl's planning prompt is ~6.5k tokens, and the activations "
            "for it dominate memory even with 4-bit weights. Options:\n"
            "  - Use a free Colab T4 (16 GB)\n"
            "  - Train the 0.5B instead: --base-model Qwen/Qwen2.5-0.5B-Instruct\n"
            "  - Shorten prompts with --max-length 2048 (loses plan context)"
        )


def load_dataset(path: Path, tokenizer, max_length: int):
    from datasets import Dataset

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"{path} is empty — run finetune.build_dataset first.")

    print(f"examples: {len(rows)}")
    if len(rows) < 500:
        print(
            f"Warning: {len(rows)} examples is small. Expect the adapter to "
            "memorise rather than generalise; add your own pairs before "
            "trusting the result."
        )

    def _format(row: dict) -> dict:
        # Train on the completion only. Including the prompt in the loss
        # teaches the model to reproduce its own instructions, which is
        # both wasteful and slightly harmful.
        text = row["prompt"] + "\n" + row["completion"] + tokenizer.eos_token
        encoded = tokenizer(
            text,
            truncation=True,
            max_length=max_length,
            padding="max_length",
        )
        prompt_len = len(
            tokenizer(row["prompt"] + "\n", truncation=True, max_length=max_length)["input_ids"]
        )
        labels = list(encoded["input_ids"])
        for i in range(min(prompt_len, len(labels))):
            labels[i] = -100  # mask the prompt
        encoded["labels"] = labels
        return encoded

    return Dataset.from_list(rows).map(_format, remove_columns=["prompt", "completion", "user_request"])


def main() -> int:
    parser = argparse.ArgumentParser(description="QLoRA fine-tune Pearl's planner.")
    parser.add_argument("--data", type=Path, default=Path("finetune/data/plans.jsonl"))
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--out", type=Path, default=Path("finetune/adapter"))
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--lr", type=float, default=2e-4)
    args = parser.parse_args()

    _check_environment()

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    print(f"base model: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        ),
        device_map="auto",
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(
        model,
        LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            # Attention and MLP projections: the standard Qwen target set.
            target_modules=[
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ],
        ),
    )
    model.print_trainable_parameters()

    dataset = load_dataset(args.data, tokenizer, args.max_length)

    trainer = Trainer(
        model=model,
        train_dataset=dataset,
        args=TrainingArguments(
            output_dir=str(args.out / "checkpoints"),
            num_train_epochs=args.epochs,
            per_device_train_batch_size=1,
            # Batch size 1 fits the long prompt; accumulation restores a
            # usable effective batch without more memory.
            gradient_accumulation_steps=8,
            gradient_checkpointing=True,
            learning_rate=args.lr,
            fp16=True,
            logging_steps=5,
            save_strategy="epoch",
            report_to=[],
        ),
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True),
    )

    trainer.train()

    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.out))
    tokenizer.save_pretrained(str(args.out))
    print(f"\nadapter saved to {args.out}")
    print("Next: merge and convert to GGUF — see finetune/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
