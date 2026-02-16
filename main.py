from fastapi import FastAPI, Request
from twilio.rest import Client
from anthropic import Anthropic
import os

app = FastAPI()

# Inicializar clientes con variables de entorno
anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))

# Store appointments in memory (upgrade to DB later)
appointments = {}

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    """Handle WhatsApp messages with detailed error logging"""
    try:
        # Get the raw form data
        form_data = await request.form()
        print("Received webhook: " + str(dict(form_data)))
        
        user_message = form_data.get('Body', '')
        from_number = form_data.get('From', '')
        
        print("Message: '" + user_message + "' from " + from_number)
        
        # Check if API keys exist
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        twilio_sid = os.getenv("TWILIO_SID")
        twilio_token = os.getenv("TWILIO_TOKEN")
        
        print("API Keys present - Anthropic: " + str(bool(anthropic_key)) + 
              ", Twilio SID: " + str(bool(twilio_sid)) + 
              ", Twilio Token: " + str(bool(twilio_token)))
        
        if not anthropic_key:
            raise Exception("ANTHROPIC_API_KEY is missing!")
        
        if not twilio_sid or not twilio_token:
            raise Exception("Twilio credentials are missing!")
        
        # Call Claude
        print("Calling Claude API...")
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": user_message}],
            system="You're a dental clinic assistant in Mexico. Respond in Spanish briefly and helpfully."
        )
        
        ai_response = response.content[0].text
        print("Claude response: " + ai_response)
        
        # Send back via Twilio Sandbox
        print("Sending WhatsApp reply...")
        message = twilio_client.messages.create(
            from_='whatsapp:+14155238886',  # Twilio Sandbox number
            body=ai_response,
            to=from_number
        )
        print("Message sent! SID: " + message.sid)
        
        return {"status": "ok"}
        
    except Exception as e:
        # This will show us exactly what went wrong
        error_msg = "ERROR: " + str(type(e).__name__) + ": " + str(e)
        print(error_msg)
        
        # Try to send an error message back to the user
        try:
            if 'from_number' in locals() and from_number:
                twilio_client.messages.create(
                    from_='whatsapp:+14155238886',
                    body="Lo siento, tengo problemas técnicos. Por favor intenta más tarde.",
                    to=from_number
                )
        except:
            pass
            
        return {"status": "error", "message": str(e)}, 500

@app.get("/")
async def root():
    return {"message": "AI Labor Center MVP is running with Claude!"}

@app.get("/health")
async def health():
    """Health check endpoint for Railway"""
    return {"status": "healthy"}

@app.get("/check-config")
async def check_config():
    """Check if all required environment variables are set"""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    twilio_sid = os.getenv("TWILIO_SID")
    twilio_token = os.getenv("TWILIO_TOKEN")
    
    return {
        "anthropic_key_exists": bool(anthropic_key),
        "anthropic_key_prefix": anthropic_key[:10] + "..." if anthropic_key else None,
        "twilio_sid_exists": bool(twilio_sid),
        "twilio_sid_prefix": twilio_sid[:10] + "..." if twilio_sid else None,
        "twilio_token_exists": bool(twilio_token),
        "message": "If any are False, add them in Railway Variables!"
    }

@app.get("/check-twilio")
async def check_twilio():
    """Verify that Twilio credentials are correct"""
    try:
        # Try to make a simple API call
        account = twilio_client.api.accounts(os.getenv("TWILIO_SID")).fetch()
        return {
            "status": "Valid credentials",
            "account_name": account.friendly_name,
            "auth_token_exists": bool(os.getenv("TWILIO_TOKEN"))
        }
    except Exception as e:
        return {
            "status": "Error with credentials",
            "error": str(e)
        }

@app.post("/test-post")
async def test_post(request: Request):
    """Test endpoint for POST requests"""
    try:
        data = await request.form()
        print("Test POST received: " + str(dict(data)))
        return {"status": "success", "received": dict(data)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# This is critical - makes it work on Railway
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    print("Starting server on port " + str(port))
    print("Binding to 0.0.0.0 - this is CORRECT for Railway")
    
    uvicorn.run(
        "main:app", 
        host="0.0.0.0",  # MUST be 0.0.0.0, not localhost!
        port=port,
        reload=False
    )
