from fastapi import FastAPI, Request
from twilio.rest import Client
from anthropic import Anthropic
import os
from datetime import datetime
import json
from sqlalchemy import create_engine, Column, String, DateTime, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
import re

# Configuración de la base de datos
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # Railway usa postgres:// pero SQLAlchemy requiere postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Configurar SQLAlchemy
engine = create_engine(DATABASE_URL, poolclass=NullPool)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Modelo de base de datos
class Conversation(Base):
    __tablename__ = "conversations"
    
    phone_number = Column(String(50), primary_key=True)
    user_data = Column(JSON)  # Guarda nombre, etc.
    history = Column(JSON)    # Guarda el historial de mensajes
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Crear tablas
Base.metadata.create_all(bind=engine)

# Inicializar app
app = FastAPI()

# Inicializar clientes de API
anthropic_client = Anthropic(api_key=os.getenv("DEEPSEEK_API_KEY"),
                            base_url="https://api.deepseek.com/v1"
)
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))

# Función para obtener o crear conversación
def get_or_create_conversation(phone_number):
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.phone_number == phone_number).first()
        
        if not conv:
            # Crear nueva conversación
            conv = Conversation(
                phone_number=phone_number,
                user_data={"name": None, "first_seen": datetime.now().isoformat()},
                history=[]
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
        
        return conv
    finally:
        db.close()

# Función para actualizar conversación
def update_conversation(phone_number, user_data=None, new_message=None, new_response=None):
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.phone_number == phone_number).first()
        if conv:
            if user_data:
                conv.user_data = user_data
            
            if new_message and new_response:
                history = conv.history or []
                history.append({
                    "user": new_message,
                    "assistant": new_response,
                    "timestamp": datetime.now().isoformat()
                })
                # Mantener solo últimos 20 mensajes
                conv.history = history[-20:]
            
            db.commit()
    finally:
        db.close()

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    try:
        form_data = await request.form()
        user_message = form_data.get('Body', '')
        from_number = form_data.get('From', '')
        
        print(f"\n📱 Mensaje de {from_number}: {user_message}")
        
        # Obtener conversación existente o crear nueva
        conversation = get_or_create_conversation(from_number)
        user_data = conversation.user_data or {}
        history = conversation.history or []
        
        print(f"👤 Datos actuales: {user_data}")
        
        # Detectar nombre si no lo tenemos
        if not user_data.get("name"):
            name_patterns = [
                r"me llamo ([A-Za-záéíóúÁÉÍÓÚ]+)",
                r"mi nombre es ([A-Za-záéíóúÁÉÍÓÚ]+)",
                r"soy ([A-Za-záéíóúÁÉÍÓÚ]+)",
                r"llamo ([A-Za-záéíóúÁÉÍÓÚ]+)"
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, user_message, re.IGNORECASE)
                if match:
                    user_data["name"] = match.group(1)
                    print(f"✅ Nombre detectado: {user_data['name']}")
                    break
        
        # Construir historial para el prompt
        history_text = ""
        for msg in history[-5:]:  # Últimos 5 mensajes para contexto
            history_text += f"Paciente: {msg['user']}\nAsistente: {msg['assistant']}\n"
        
        # Sistema prompt con contexto
        system_prompt = f"""Eres un asistente virtual para una clínica dental en México llamada "Sonrisa Perfecta".

INFORMACIÓN DEL PACIENTE:
- Nombre: {user_data.get('name', 'No proporcionado')}
- Teléfono: {from_number}

HORARIOS DE ATENCIÓN:
- Lunes a Viernes: 9:00 AM - 6:00 PM
- Sábados: 9:00 AM - 2:00 PM
- Domingos: Cerrado

SERVICIOS:
- Limpieza dental ($800 MXN)
- Extracciones ($1,200 MXN)
- Blanqueamiento ($2,500 MXN)
- Consulta general ($500 MXN)

HISTORIAL RECIENTE:
{history_text}

INSTRUCCIONES:
- Usa el nombre del paciente si lo conoces
- Mantén el contexto de la conversación
- Sé amable y profesional en español de México
- Ofrece horarios disponibles cuando pidan citas
- Pregunta qué servicio necesitan
- Si no sabes algo, ofrece tomar nota y que te contactarán"""

        # Llamar a Claude
        print("🤖 Llamando a Claude...")
        response = anthropic_client.messages.create(
            model="deepseek-chat",
            max_tokens=500,
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt
        )
        
        ai_response = response.content[0].text
        print(f"💬 Respuesta: {ai_response}")
        
        # Guardar en base de datos
        update_conversation(
            phone_number=from_number,
            user_data=user_data,
            new_message=user_message,
            new_response=ai_response
        )
        
        # Enviar respuesta por WhatsApp
        message = twilio_client.messages.create(
            from_='whatsapp:+14155238886',
            body=ai_response,
            to=from_number
        )
        
        print("✅ Mensaje enviado")
        return {"status": "ok"}
        
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}, 500

# Endpoints de administración
@app.get("/admin/conversations")
async def list_conversations(limit: int = 10):
    """Lista las conversaciones recientes (solo para administración)"""
    db = SessionLocal()
    try:
        conversations = db.query(Conversation).order_by(Conversation.updated_at.desc()).limit(limit).all()
        result = []
        for conv in conversations:
            result.append({
                "phone": conv.phone_number,
                "user_data": conv.user_data,
                "message_count": len(conv.history or []),
                "last_message": conv.history[-1] if conv.history else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None
            })
        return {"conversations": result}
    finally:
        db.close()

@app.get("/admin/conversation/{phone_number}")
async def get_conversation(phone_number: str):
    """Obtiene una conversación específica"""
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.phone_number == phone_number).first()
        if conv:
            return {
                "phone": conv.phone_number,
                "user_data": conv.user_data,
                "history": conv.history,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None
            }
        return {"error": "Conversación no encontrada"}, 404
    finally:
        db.close()

@app.get("/")
async def root():
    return {
        "message": "AI Labor Center con PostgreSQL",
        "status": "running",
        "database": "connected" if DATABASE_URL else "not configured"
    }

@app.get("/health")
async def health():
    """Health check que también verifica la DB"""
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        db_status = "connected"
    except:
        db_status = "error"
    
    return {
        "status": "healthy",
        "database": db_status,
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"🚀 Iniciando servidor en puerto {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
