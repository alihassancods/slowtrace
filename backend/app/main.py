from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

app = FastAPI(title="SlowTrace", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}
