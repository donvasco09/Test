from fastapi import FastAPI, Request
from twilio.rest import Client
from openai import OpenAI
import os
from datetime import datetime
import json
from sqlalchemy import create_engine, Column, String, DateTime, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
import re
import sys
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

logger.info("🔍 Iniciando aplicación...")

# ========== VALIDACIÓN DE VARIABLES DE ENTORNO ==========
logger.info("Verificando variables de entorno...")

required_vars = {
    "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY"),
    "TWILIO_SID": os.getenv("TWILIO_SID"),
    "TWILIO_TOKEN": os.getenv("TWILIO_TOKEN"),
    "DATABASE_URL": os.getenv("DATABASE_URL")
}

missing_vars = [name for name, value in required_vars.items() if not value]
if missing_vars:
    logger.error(f"❌ ERROR CRÍTICO: Faltan variables: {missing_vars}")
    logger.error("💡 Configúralas en Railway > Variables")
    sys.exit(1)

logger.info("✅ Todas las variables están presentes")

# ========== CONFIGURACIÓN DE BASE DE DATOS ==========
DATABASE_URL = os.getenv("DATABASE_URL")
logger.info(f"📊 Conectando a base de datos...")

try:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        logger.info("🔄 URL convertida a postgresql://")
    
    # Configurar SQLAlchemy
    engine = create_engine(
        DATABASE_URL, 
        poolclass=NullPool,
        connect_args={"connect_timeout": 10}  # Timeout de conexión
    )
    
    # Probar conexión
    with engine.connect() as conn:
        conn.execute("SELECT 1")
    logger.info("✅ Conexión a base de datos exitosa")
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    
except Exception as e:
    logger.error(f"❌ ERROR conectando a base de datos: {str(e)}")
    logger.error("💡 Verifica que PostgreSQL esté agregado al proyecto")
    sys.exit(1)

# ========== MODELO DE BASE DE DATOS ==========
class Conversation(Base):
    __tablename__ = "conversations"
    
    phone_number = Column(String(50), primary_key=True)
    user_data = Column(JSON, default={})
    history = Column(JSON, default=[])
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Crear tablas (safe create)
try:
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Tablas verificadas/creadas")
except Exception as e:
    logger.error(f"❌ Error creando tablas: {str(e)}")
    sys.exit(1)

# ========== INICIALIZAR CLIENTES ==========
logger.info("Inicializando clientes API...")

try:
    deepseek_client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1"
    )
    logger.info("✅ DeepSeek client inicializado")
except Exception as e:
    logger.error(f"❌ Error inicializando DeepSeek: {str(e)}")
    sys.exit(1)

try:
    twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))
    logger.info("✅ Twilio client inicializado")
except Exception as e:
    logger.error(f"❌ Error inicializando Twilio: {str(e)}")
    sys.exit(1)

# ========== FUNCIONES DE BASE DE DATOS ==========
def get_or_create_conversation(phone_number):
    """Obtiene o crea una conversación"""
    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.phone_number == phone_number).first()
        
        if not conv:
            conv = Conversation(
                phone_number=phone_number,
                user_data={"name": None, "first_seen": datetime.now().isoformat()},
                history=[]
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
            logger.info(f"🆕 Nueva conversación creada para {phone_number}")
        
        return conv
    except Exception as e:
        logger.error(f"❌ Error en get_or_create_conversation: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()

def update_conversation(phone_number, user_data=None, new_message=None, new_response=None):
    """Actualiza una conversación"""
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
                conv.history = history[-20:]  # Mantener últimos 20
            
            db.commit()
    except Exception as e:
        logger.error(f"❌ Error en update_conversation: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()

# ========== INICIALIZAR FASTAPI ==========
app = FastAPI(title="WhatsApp Dental Bot", version="1.0.0")
logger.info("✅ FastAPI app creada")

# ========== ENDPOINTS ==========
@app.get("/")
async def root():
    return {
        "message": "WhatsApp Dental Bot",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health():
    """Health check completo"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "checks": {}
    }
    
    # Verificar base de datos
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        health_status["checks"]["database"] = "connected"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Verificar APIs
    health_status["checks"]["deepseek_api"] = "configured" if os.getenv("DEEPSEEK_API_KEY") else "missing"
    health_status["checks"]["twilio_api"] = "configured" if os.getenv("TWILIO_SID") and os.getenv("TWILIO_TOKEN") else "missing"
    
    return health_status

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    """Webhook para mensajes de WhatsApp"""
    try:
        form_data = await request.form()
        user_message = form_data.get('Body', '').strip()
        from_number = form_data.get('From', '')
        
        logger.info(f"📱 Mensaje de {from_number}: {user_message}")
        
        if not user_message or not from_number:
            logger.warning("⚠️ Mensaje vacío o sin número")
            return {"status": "error", "message": "Invalid request"}, 400
        
        # Obtener conversación
        conversation = get_or_create_conversation(from_number)
        user_data = conversation.user_data or {}
        history = conversation.history or []
        
        # Detectar nombre
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
                    logger.info(f"✅ Nombre detectado: {user_data['name']}")
                    break
        
        # Construir historial para contexto
        history_text = ""
        for msg in history[-5:]:
            history_text += f"Paciente: {msg['user']}\nAsistente: {msg['assistant']}\n"
        
        # Sistema prompt
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

        # Llamar a DeepSeek
        logger.info("🤖 Llamando a DeepSeek API...")
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        logger.info(f"💬 Respuesta generada: {ai_response[:100]}...")
        
        # Guardar en BD
        update_conversation(
            phone_number=from_number,
            user_data=user_data,
            new_message=user_message,
            new_response=ai_response
        )
        
        # Enviar por WhatsApp
        logger.info("📤 Enviando respuesta por WhatsApp...")
        message = twilio_client.messages.create(
            from_='whatsapp:+14155238886',
            body=ai_response,
            to=from_number
        )
        
        logger.info(f"✅ Mensaje enviado (SID: {message.sid})")
        return {"status": "ok", "message_sid": message.sid}
        
    except Exception as e:
        logger.error(f"❌ ERROR en webhook: {str(e)}", exc_info=True)
        return {"status": "error", "message": "Internal server error"}, 500

# ========== ENDPOINTS DE ADMINISTRACIÓN ==========
@app.get("/admin/conversations")
async def list_conversations(limit: int = 10):
    """Lista las conversaciones recientes"""
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

# ========== PUNTO DE ENTRADA ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"🚀 Iniciando servidor en puerto {port}")
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=port,
        log_level="info"
    )
