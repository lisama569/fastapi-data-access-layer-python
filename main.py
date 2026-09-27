import os

import uvicorn

from app.api import create_app

DB_PATH = os.environ.get("APP_DB_PATH", "app.db")

app = create_app(DB_PATH)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
