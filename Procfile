web: gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --timeout 300 --graceful-timeout 20 --worker-class gthread --workers 4 --threads 8 --max-requests 1000 --max-requests-jitter 100
