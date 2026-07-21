from tools.registry import registry
from config.settings import settings


def hello():
    return f"Hello from {settings.PROJECT_NAME}"


registry.register("hello", hello)


def main():
    print("Registered Tools")
    print("----------------")
    print(registry.list())
    print(registry.get("hello")())


if __name__ == "__main__":
    main()