from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

from config.settings import settings


class ModelLoader:
    def __init__(self):
        self.model = None
        self.tokenizer = None

    def load(self):
        if self.model is not None:
            return

        print(f"\nDownloading/Loading: {settings.DEFAULT_MODEL}")

        self.tokenizer = AutoTokenizer.from_pretrained(
            settings.DEFAULT_MODEL
        )

        self.model = AutoModelForCausalLM.from_pretrained(
            settings.DEFAULT_MODEL,
            dtype=torch.float16 if settings.DEVICE == "cuda" else torch.float32,
            device_map="auto",
            low_cpu_mem_usage=True,
        )

        print("✅ Model Ready")


loader = ModelLoader()