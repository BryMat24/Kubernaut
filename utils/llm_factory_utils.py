from langchain_openrouter import ChatOpenRouter
import os

def create_llm_model(model: str):
    return ChatOpenRouter(
        model=model,
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY")
    )