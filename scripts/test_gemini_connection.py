from app.services.gemini_client import GeminiClient


def main() -> None:
    result = GeminiClient().test_connection()
    print(result)


if __name__ == "__main__":
    main()
