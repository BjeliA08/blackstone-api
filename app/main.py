from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import auth, me, director, admin, sos

app = FastAPI(
    title="Blackstone Security API",
    description="Backend for Blackstone Security operations — auth, scheduling, check-in/out.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(director.router)
app.include_router(admin.router)
app.include_router(sos.router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
