"""AWS Lambda handler — wraps the FastAPI app via Mangum."""
from mangum import Mangum
from src.api.app import app

handler = Mangum(app, lifespan="off")
