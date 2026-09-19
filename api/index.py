from fastapi import FastAPI

app = FastAPI()


@app.get("/api")
def home():
    return {
        "status": "online",
        "service": "Lumi AI 2.0"
    }
