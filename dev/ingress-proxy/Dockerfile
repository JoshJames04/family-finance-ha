FROM python:3.13-slim
RUN pip install --no-cache-dir fastapi httpx uvicorn
COPY proxy.py /proxy.py
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8400"]
