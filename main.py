import os
import asyncio
import uuid
import json
import re
import google.generativeai as genai
import httpx
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings
from typing import List, Optional

# --- Configuration ---
class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    gemini_api_key: str
    blockchain_service_url: str = "http://127.0.0.1:8001"

    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()

# --- Initialize Clients ---
supabase: Client = create_client(settings.supabase_url, settings.supabase_key)
genai.configure(api_key=settings.gemini_api_key)

# --- Initialize FastAPI App ---
app = FastAPI(
    title="Jharkhand Tourism MVP Backend",
    description="Main API for the Jharkhand Tourism hackathon project.",
    version="1.0.0"
)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Chatbot Knowledge Base & State ---
try:
    with open("places.json", "r") as f:
        places = json.load(f)
except FileNotFoundError:
    print("WARNING: places.json not found. Chatbot knowledge base will be empty.")
    places = {}

interest_map = {
    "nature": ["netarhat", "patratu", "hundru"],
    "wildlife": ["betla"],
    "pilgrimage": ["deoghar"]
}
greeted_users = set()


# --- Pydantic Models ---

class Product(BaseModel):
    id: int
    name: str
    description: str
    image_url: Optional[str] = Field(default=None, alias="image_url")
    price: float
    artisan_name: str = Field(alias="artisan_name")

    class Config:
        populate_by_name = True

class TransactionRequest(BaseModel):
    product_id: int = Field(..., alias="productId")
    price: float

class ActivityLogRequest(BaseModel):
    user_id: Optional[str] = "guest"
    action: str

class ChatRequest(BaseModel):
    user_id: Optional[str] = "default"
    message: str

class SaleReceipt(BaseModel):
    productID: str
    price: str
    timestamp: str
    txID: str


# --- Chatbot Helper Function ---
async def query_gemini(message: str, max_tokens: int = 250) -> str:
    model = genai.GenerativeModel("gemini-1.5-flash")
    try:
        prompt = (
            "First, determine if the following user query is related to Jharkhand tourism, travel, or local culture. "
            "If it is NOT related, your only response must be the exact string 'OUT_OF_CONTEXT'. "
            "If it IS related, answer the question briefly and concisely, in 2-3 sentences, as a helpful tourism assistant. "
            f"User Query: '{message}'"
        )
        
        response = await model.generate_content_async(
            prompt,
            generation_config={"max_output_tokens": max_tokens}
        )
        
        finish_reason = response.candidates[0].finish_reason.name
        reply = response.text or ""
        reply = reply.strip()

        if reply == "OUT_OF_CONTEXT":
            return "I can only answer questions about Jharkhand tourism. How can I help you with your trip?"

        if finish_reason == "STOP":
            reply = re.sub(r"\n{2,}", "\n", reply)
            return reply
        else:
            return "I couldn't complete the response. Please try rephrasing your question."

    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return "Sorry, an error occurred while contacting the AI model."


# --- API Endpoints ---

@app.get("/")
async def root():
    return {"message": "Jharkhand Tourism Main API is running 🚀"}

@app.post("/chat")
async def chat(request: ChatRequest):
    user_id = request.user_id
    user_message = request.message.lower()

    if user_id not in greeted_users:
        greeted_users.add(user_id)
        return {"reply": "👋 Hello! Welcome to Jharkhand Tourism Chatbot. How can I help you today?"}

    # --- Local Knowledge Base Logic ---
    if "itinerary" in user_message or re.search(r"plan.*day", user_message):
        days_match = re.search(r"(\d+)\s*day", user_message)
        days = int(days_match.group(1)) if days_match else 3
        interests = [i for i in interest_map if i in user_message] or ["nature"]
        selected_places = [p for i in interests for p in interest_map.get(i, [])] or list(places.keys())
        
        if not selected_places:
             return {"reply": "I couldn't find any places matching your interests."}

        plan = {}
        for i in range(days):
            place_name = selected_places[i % len(selected_places)]
            info = places.get(place_name, {})
            plan[f"Day {i+1}"] = (
                f"📍 {place_name.capitalize()} - {info.get('description', 'N/A')}\n"
                f"   🕒 Best time: {info.get('best_time', 'N/A')}\n"
                f"   🎯 Activities: {info.get('activities', 'N/A')}"
            )
        return {"reply": "\n\n".join(plan.values())}

    for place, info in places.items():
        if place in user_message:
            return {"reply": f"{place.capitalize()}: {info['description']}"}

    # --- Fallback to Gemini ---
    try:
        gemini_reply = await query_gemini(user_message)
        return {"reply": gemini_reply}
    except Exception as e:
        print(f"Error in chat logic: {e}")
        return {"reply": "❌ Sorry, I had trouble processing your request."}

@app.get("/products", response_model=List[Product])
def get_products():
    try:
        response = supabase.table('products').select('*').order('id').execute()
        return [Product(**p) for p in response.data]
    except Exception as e:
        print(f"An unexpected error occurred while fetching products: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")

@app.post("/record-transaction", response_model=SaleReceipt)
async def record_transaction(transaction: TransactionRequest):
    """
    Records a transaction by forwarding the request to the dedicated blockchain microservice.
    """
    try:
        print(f"INFO (Main App): Forwarding transaction to blockchain service...")
        
        payload = {
            "product_id": str(transaction.product_id),
            "price": str(transaction.price)
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.blockchain_service_url}/record-transaction-on-chain",
                json=payload,
                timeout=30.0 
            )
        
        response.raise_for_status() 
        
        return response.json()

    except httpx.RequestError as e:
        print(f"An error occurred while requesting blockchain service: {e}")
        raise HTTPException(status_code=503, detail="The blockchain service is unavailable.")
    except Exception as e:
        print(f"An unexpected error occurred during transaction forwarding: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred.")

# UPDATED: Endpoint with detailed logging for debugging
@app.get("/verify-transaction", response_model=SaleReceipt)
async def verify_transaction(product_id: str, tx_id: str):
    """
    Verifies a transaction by querying the blockchain microservice and finding the matching record.
    """
    service_url = f"{settings.blockchain_service_url}/query/sales/{product_id}"
    print("\n--- VERIFICATION PROCESS STARTED ---")
    print(f"INFO (Main App): Verifying tx_id '{tx_id}' for product_id '{product_id}'")
    print(f"INFO (Main App): Calling blockchain service at: {service_url}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(service_url, timeout=10.0)
        
        print(f"INFO (Main App): Received response from blockchain service. Status: {response.status_code}")
        print(f"INFO (Main App): Raw response body: {response.text}")

        response.raise_for_status()
        
        sales = response.json()
        print(f"INFO (Main App): Successfully parsed JSON response.")

        if not isinstance(sales, list):
            print(f"ERROR (Main App): Response from blockchain service is not a list. Type is: {type(sales)}")
            raise HTTPException(status_code=500, detail="Invalid response format from blockchain service.")

        print(f"INFO (Main App): Searching for tx_id '{tx_id}' in {len(sales)} sale record(s)...")
        for sale in sales:
            print(f"  - Checking record with txID: {sale.get('txID')}")
            if sale.get("txID") == tx_id:
                print("SUCCESS (Main App): Verification successful. Transaction found.")
                print("--- VERIFICATION PROCESS ENDED ---\n")
                return sale
        
        print(f"WARN (Main App): Verification failed. Transaction ID '{tx_id}' not found for this product.")
        print("--- VERIFICATION PROCESS ENDED ---\n")
        raise HTTPException(status_code=404, detail="Transaction ID not found for the given product.")

    except httpx.RequestError as e:
        print(f"ERROR (Main App): Could not connect to blockchain service. Is it running? Error: {e}")
        print("--- VERIFICATION PROCESS ENDED ---\n")
        raise HTTPException(status_code=503, detail="The blockchain service is unavailable.")
    
    except httpx.HTTPStatusError as e:
        print(f"ERROR (Main App): Blockchain service returned an error. Status: {e.response.status_code}. Body: {e.response.text}")
        print("--- VERIFICATION PROCESS ENDED ---\n")
        raise HTTPException(status_code=502, detail=f"An error occurred in the blockchain service: {e.response.text}")

    except json.JSONDecodeError:
        print(f"ERROR (Main App): Failed to decode JSON response from blockchain service.")
        print("--- VERIFICATION PROCESS ENDED ---\n")
        raise HTTPException(status_code=500, detail="Received an invalid (non-JSON) response from the blockchain service.")

    except Exception as e:
        print(f"ERROR (Main App): An unexpected error occurred during verification: {e}")
        print("--- VERIFICATION PROCESS ENDED ---\n")
        raise HTTPException(status_code=500, detail="An internal error occurred during verification.")

@app.post("/log-activity")
def log_activity(activity: ActivityLogRequest):
    try:
        supabase.table('user_activity_log').insert({
            'user_id': activity.user_id,
            'action': activity.action
        }).execute()
        return {"status": "success", "message": "Activity logged."}
    except Exception as e:
        print(f"An error occurred while logging activity: {e}")
        raise HTTPException(status_code=500, detail="Failed to log activity.")

