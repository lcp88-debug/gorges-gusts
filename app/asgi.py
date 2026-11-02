import logging

from app.main import create_app

logging.basicConfig(level=logging.INFO)

app = create_app()
