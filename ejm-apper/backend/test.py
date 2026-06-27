import asyncio
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
import os

load_dotenv()
client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

async def test():
    msg = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{"role": "user", "content": "Say hello"}]
    )
    print(msg.content[0].text)

asyncio.run(test())