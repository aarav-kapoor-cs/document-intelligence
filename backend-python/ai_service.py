import os

from openai import AzureOpenAI

from models import TokenUsage

# Sends the document text + your prompt to Azure OpenAI and returns the answer
# plus how many tokens were used (input, output, total).


# Returns (answer_text, TokenUsage or None).
def answer(prompt, document_text):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    key = os.getenv("AZURE_OPENAI_KEY", "")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    # No prompt, or AI not set up yet -> skip the AI step.
    if not prompt or not endpoint.startswith("http"):
        return "", None

    client = AzureOpenAI(azure_endpoint=endpoint, api_key=key, api_version=api_version)
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": document_text},
        ],
    )

    answer_text = response.choices[0].message.content or ""
    tokens = TokenUsage(
        prompt_tokens=response.usage.prompt_tokens,       # input tokens
        completion_tokens=response.usage.completion_tokens,  # output tokens
        total_tokens=response.usage.total_tokens,
    )
    return answer_text, tokens
