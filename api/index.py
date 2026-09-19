from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def home():
    return {
        "status": "online",
        "service": "Lumi AI 2.0"
    }
