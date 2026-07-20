from openrouter import OpenRouter
import os


def generate_title(query: str):
    with OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", "")) as client:
        response = client.chat.send(
            model="openai/gpt-5-nano",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Generate a concise title for this chat. "
                                "Use at most 5 words. "
                                "Return only the title, with no quotes, punctuation, or explanation.\n\n"
                                f"User message: {query}"
                            ),
                        },
                    ],
                }
            ],
        )

    return response.choices[0].message.content.strip()