import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app import models  # noqa: F401 — register all tables for create_all
from app.database import Base, engine
from app.routers import webhook

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Food Fusion — Order-Placement Assistant")

app.include_router(webhook.router)


@app.on_event("startup")
def on_startup():
    # Dev convenience — swap for Alembic migrations once the schema stabilizes.
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}

PRIVACY_POLICY_HTML = """
<!DOCTYPE html>
<html>
<head><title>Food Fusion — Privacy Policy</title></head>
<body style="font-family: sans-serif; max-width: 600px; margin: 40px auto; line-height: 1.6;">
<h1>Privacy Policy — Food Fusion WhatsApp Ordering Assistant</h1>
<p>Last updated: October 2026</p>

<p>Food Fusion ("we", "us") operates a WhatsApp-based ordering assistant for our restaurant
located at Level 2, Arfa Software Technology Park, Lahore.</p>

<h2>What we collect</h2>
<p>When you message our WhatsApp number, we store your WhatsApp phone number, the text of
your messages, and the orders you place (items, quantities, and totals) in order to process
your order and maintain conversation context across messages.</p>

<h2>How we use it</h2>
<p>This information is used solely to take and fulfill your order, communicate order status
(such as confirmation or rejection), and improve our service. We do not sell or share your
information with third parties for marketing purposes.</p>

<h2>Third-party services</h2>
<p>Your messages are processed using WhatsApp's Cloud API (Meta), and natural-language
understanding is provided by third-party AI language model providers. Order data is stored
securely in a managed database.</p>

<h2>Data retention</h2>
<p>Conversation history is retained to support ongoing order-related interactions. You may
contact us to request deletion of your data.</p>

<h2>Contact</h2>
<p>For questions about this policy, contact us at farhadalifarhat@gmail.com.</p>
</body>
</html>
"""


@app.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy():
    return PRIVACY_POLICY_HTML
