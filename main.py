from fastapi import FastAPI, Request
from twilio.rest import Client
from anthropic import Anthropic  # Changed import
import os

app = FastAPI()

# Initialize Claude client (instead of OpenAI)
anthropic_client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")  # Changed env variable
)

twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))

# Store appointments in memory (upgrade to DB later)
appointments = {}

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    """Handle WhatsApp messages"""
    data = await request.form()
    user_message = data.get('Body', '')
    from_number = data.get('From', '')
    
    # Claude API call (different format from OpenAI)
    response = anthropic_client.messages.create(
        model="claude-3-5-sonnet-20241022",  # Latest Claude model
        max_tokens=1024,
        messages=[
            {
                "role": "user", 
                "content": user_message
            }
        ],
        system="You're a dental clinic assistant in Mexico. Be helpful, concise, and professional in Spanish."  # System prompt
    )
    
    # Extract the response text (Claude returns content differently)
    ai_response = response.content[0].text
    
    # Send via WhatsApp
    message = twilio_client.messages.create(
        from_='whatsapp:+14155238886',
        body=ai_response,
        to=from_number
    )
    
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "AI Labor Center MVP is running with Claude!"}

# Add at the VERY END of main.py
if __name__ == "__main__":
    import uvicorn
    import os
    
    # Railway sets the PORT environment variable automatically
    port = int(os.getenv("PORT", 8000))
    print(f"Starting server on port {port}")
    
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=port,
        reload=False
    )
