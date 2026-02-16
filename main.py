from fastapi import FastAPI, Request
from twilio.rest import Client
from openai import OpenAI
import os

app = FastAPI()
openai_client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))

# Store appointments in memory (upgrade to DB later)
appointments = {}

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    """Handle WhatsApp messages"""
    data = await request.form()
    user_message = data.get('Body', '')
    from_number = data.get('From', '')
    
    # Simple AI response
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You're a dental clinic assistant in Mexico."},
            {"role": "user", "content": user_message}
        ]
    )
    
    ai_response = response.choices[0].message.content
    
    # Send via WhatsApp
    message = twilio_client.messages.create(
        from_='whatsapp:+12676510310',
        body=ai_response,
        to=from_number
    )
    
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "AI Labor Center MVP is running!"}
