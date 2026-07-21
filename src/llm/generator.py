import torch

from llm.model import loader


SYSTEM_PROMPT = """
You are My MCP.

You are an AI coding assistant.

Rules:
- Answer only what the user asks.
- Never repeat the user's prompt.
- Return only the final answer.
- Be concise.
"""


class Generator:

    def generate(self, prompt: str) -> str:

        tokenizer = loader.tokenizer
        model = loader.model

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        inputs = tokenizer(
            text,
            return_tensors="pt",
        ).to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=256,
                temperature=0.2,
                top_p=0.95,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
            )

        generated_tokens = outputs[0][inputs["input_ids"].shape[1]:]

        response = tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        )

        return response.strip()


generator = Generator()